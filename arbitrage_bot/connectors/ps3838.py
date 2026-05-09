import asyncio
import base64
import logging
import aiohttp
from ..config import PS3838_USERNAME, PS3838_PASSWORD, PS3838_BASE_URL

logger = logging.getLogger(__name__)

# PS3838 sport IDs (Canonical)
SPORT_IDS = {
    3:  "baseball",
    4:  "basketball",
    15: "football", # American Football
    6:  "boxing",
    33: "tennis",
    8:  "hockey",
    29: "soccer",
    12: "rugby",
    18: "volleyball",
    10: "handball",
}

from ..config import SPORTS_TO_MONITOR
ACTIVE_SPORT_IDS = SPORTS_TO_MONITOR


class PS3838Client:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.base = PS3838_BASE_URL
        self._username = PS3838_USERNAME
        self._password = PS3838_PASSWORD
        self._headers = self._build_headers()

    def _build_headers(self) -> dict:
        token = base64.b64encode(f"{self._username}:{self._password}".encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

    async def _get(self, path: str, params: dict = None, retries: int = 2) -> dict:
        import time
        start = time.time()
        for i in range(retries + 1):
            try:
                async with self.session.get(
                    f"{self.base}{path}", headers=self._headers, params=params or {}
                ) as r:
                    if r.status in (401, 403):
                        logger.critical("PS3838 AUTH ERROR: Status %d. Check your credentials!", r.status)
                        raise Exception(f"Unauthorized: Access to PS3838 denied (status {r.status})")
                    r.raise_for_status()
                    latency = (time.time() - start) * 1000
                    logger.debug("PS3838 GET %s took %.2fms", path, latency)
                    text = await r.text()
                    if not text.strip():
                        return {}
                    return await r.json(content_type=None)
            except Exception as e:
                if i == retries:
                    logger.warning("PS3838 GET %s failed after %d retries: %s", path, retries, e)
                    raise
                await asyncio.sleep(0.5 * (i + 1))
        return {}

    async def get_balance(self) -> dict:
        return await self._get("/v1/client/balance")

    async def get_line(
        self,
        event_id: int,
        sport_id: int,
        league_id: int,
        period: int,
        bet_type: str,
        outcome: str,
        handicap: float = None,
    ) -> dict:
        params = {
            "sportId": sport_id,
            "leagueId": league_id,
            "eventId": event_id,
            "periodNumber": period,
            "betType": bet_type.upper(),
            "oddsFormat": "Decimal",
        }
        if outcome.lower() in ("over", "under"):
            params["side"] = outcome.upper()
        else:
            params["team"] = outcome.upper()

        if handicap is not None:
            params["handicap"] = handicap

        return await self._get("/v1/line", params)

    async def place_bet(
        self,
        event_id: int,
        period: int,
        bet_type: str,       # "moneyline" | "spread" | "total"
        outcome: str,        # "home" | "away" | "draw" | "over" | "under"
        price: float,        # decimal odds
        stake: float,        # dollar amount
        unique_id: str,
        line_id: int,
        sport_id: int = None,
        accept_better_line: bool = False,
    ) -> dict:
        # Standard Pinnacle Straight Bet Schema
        body = {
            "uniqueRequestId": unique_id,
            "acceptBetterLine": accept_better_line,
            "oddsFormat": "Decimal",
            "stake": round(stake, 2),
            "winRiskStake": "RISK",
            "sportId": sport_id,
            "eventId": event_id,
            "periodNumber": period,
            "betType": bet_type.upper(),
            "team": outcome.upper() if outcome not in ("over", "under") else None,
            "side": outcome.upper() if outcome in ("over", "under") else None,
            "lineId": line_id,
        }

        # Remove None values
        body = {k: v for k, v in body.items() if v is not None}

        async with self.session.post(
            f"{self.base}/v1/bets/place", headers=self._headers, json=body
        ) as r:
            if r.status == 400:
                error_data = await r.json(content_type=None)
                logger.error("PS3838 Bet Placement 400 Error: %s", error_data)
                return {"status": "REJECTED", "error_code": error_data.get("code"), "raw": error_data}

            r.raise_for_status()
            resp = await r.json(content_type=None)

        if resp.get("status") == "ACCEPTED":
            return {
                "bet_id": resp.get("betId"),
                "status": "ACCEPTED",
                "price": resp.get("price", price),
                "stake": resp.get("stake", stake),
            }
        else:
            return {
                "status": resp.get("status", "REJECTED"),
                "error_code": resp.get("errorCode"),
                "raw": resp
            }

    async def get_sports(self) -> list:
        data = await self._get("/v3/sports")
        return data.get("sports", [])

    async def get_live_odds(self, sport_id: int, max_staleness_sec: int = 15) -> list:
        # Rate limit tracking (simplistic per-instance)
        if not hasattr(self, "_last_call"):
            self._last_call = {}
        if not hasattr(self, "_last_since"):
            self._last_since = {}

        now = time.time()
        last_call_ts = self._last_call.get(sport_id, 0)

        # We enforce at least a 5s delay even for deltas to be safe,
        # though the new limit is 120s. We'll warn if it's too fast.
        if now - last_call_ts < 5:
            return []

        self._last_call[sport_id] = now
        since = self._last_since.get(sport_id)

        async def _fetch_fixtures():
            params = {"sportId": sport_id, "isLive": 1}
            # Delta Fixtures can be tricky because we need to keep a full state.
            # For now, we always get full fixtures to avoid complex state management,
            # but we respect the 120s limit for full snapshots if we can.
            data = await self._get("/v3/fixtures", params)
            result = {}
            if not data: return {}
            for league in data.get("league", []):
                league_name = league.get("name", "")
                for event in league.get("events", []):
                    # Only include events that are truly live and accepting bets
                    if event.get("status") == "O": # 'O' for Open
                        result[event["id"]] = {
                            "home": event.get("home", ""),
                            "away": event.get("away", ""),
                            "league": league_name,
                            "league_id": league.get("id"),
                            "starts": event.get("starts", ""),
                            "live": event.get("liveScore", {}),
                        }
            return result

        async def _fetch_odds():
            params = {"sportId": sport_id, "isLive": 1, "oddsFormat": "Decimal"}
            if since:
                params["since"] = since
            return await self._get("/v3/odds", params)

        # Sequential fetch
        fixtures = await _fetch_fixtures()
        if not fixtures:
            return []
        data = await _fetch_odds()
        if not data:
            return []

        # Update since token for next call
        new_since = data.get("last")
        if new_since:
            self._last_since[sport_id] = new_since
            logger.debug("PS3838 Sport %d updated since token: %s", sport_id, new_since)

        events = []
        for league in data.get("leagues", []):
            for event in league.get("events", []):
                event_id = event["id"]
                fixture = fixtures.get(event_id, {})
                if not fixture:
                    continue

                periods = []
                for period in event.get("periods", []):
                    p = {
                        "number": period.get("number"),
                        "status": period.get("status"),
                        "moneyline": period.get("moneyline"),
                        "spreads": period.get("spreads", []),
                        "totals": period.get("totals", []),
                        "line_id": period.get("lineId"), # Main lineId for period (often used for ML)
                        "max_moneyline": period.get("maxMoneyline") or 0,
                        "max_spread":    period.get("maxSpread")    or 0,
                        "max_total":     period.get("maxTotal")     or 0,
                    }

                    # For Spreads and Totals, the lineId is usually inside each item in the list
                    # We'll keep them as they are but ensure they are captured

                    if p["moneyline"] or p["spreads"] or p["totals"]:
                        periods.append(p)

                home_name = fixture["home"]
                away_name = fixture["away"]
                if "(Corners)" in home_name or "(Corners)" in away_name:
                    continue

                if periods:
                    events.append({
                        "id": event_id,
                        "sport_id": sport_id,
                        "home": home_name,
                        "away": away_name,
                        "league": fixture["league"],
                        "league_id": fixture["league_id"],
                        "starts": fixture["starts"],
                        "live_score": fixture["live"],
                        "periods": periods,
                    })
        return events

    async def get_all_live_events(self) -> list:
        tasks = [self.get_live_odds(sid) for sid in ACTIVE_SPORT_IDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_events = []
        for sport_id, result in zip(ACTIVE_SPORT_IDS, results):
            if isinstance(result, Exception):
                logger.warning("PS3838 sport %d error: %s", sport_id, result)
            else:
                all_events.extend(result)
                logger.debug("PS3838 sport %d: %d live events", sport_id, len(result))
        return all_events
