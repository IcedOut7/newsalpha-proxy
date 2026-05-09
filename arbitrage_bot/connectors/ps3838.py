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


def _auth_header() -> str:
    token = base64.b64encode(f"{PS3838_USERNAME}:{PS3838_PASSWORD}".encode()).decode()
    return f"Basic {token}"


HEADERS = {
    "Authorization": _auth_header(),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


class PS3838Client:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.base = PS3838_BASE_URL

    async def _get(self, path: str, params: dict = None, retries: int = 2) -> dict:
        import time
        start = time.time()
        for i in range(retries + 1):
            try:
                async with self.session.get(
                    f"{self.base}{path}", headers=HEADERS, params=params or {}
                ) as r:
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

    async def place_bet(
        self,
        event_id: int,
        period: int,
        bet_type: str,       # "moneyline" | "spread" | "total"
        outcome: str,        # "home" | "away" | "draw" | "over" | "under"
        price: float,        # decimal odds
        stake: float,        # dollar amount
        unique_id: str,
        sport_id: int = None, # Added sport_id
        accept_better_line: bool = False,
    ) -> dict:
        fill = {
            "sportId": sport_id, # Fixed: now passing sport_id instead of None
            "eventId": event_id,
            "periodNumber": period,
            "betType": bet_type,
            "team": outcome,
            "price": price,
            "stake": round(stake, 2),
        }
        body = {
            "uniqueRequestId": unique_id,
            "acceptBetterLine": accept_better_line,
            "oddsFormat": "Decimal",
            "fills": [fill],
        }
        async with self.session.post(
            f"{self.base}/v1/bets", headers=HEADERS, json=body
        ) as r:
            r.raise_for_status()
            resp = await r.json(content_type=None)
        bets = resp.get("betResponses", [{}])
        if not bets:
            return {"status": "NO_RESPONSE", "stake": 0, "price": price}
        b = bets[0]
        return {
            "bet_id": b.get("betId"),
            "status": b.get("status", "UNKNOWN"),
            "price": b.get("price", price),
            "stake": b.get("stake", 0),
            "error_code": b.get("errorCode"),
        }

    async def get_sports(self) -> list:
        data = await self._get("/v3/sports")
        return data.get("sports", [])

    async def get_live_odds(self, sport_id: int) -> list:
        async def _fetch_fixtures():
            data = await self._get("/v3/fixtures", {"sportId": sport_id, "isLive": 1})
            result = {}
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
            return await self._get("/v3/odds", {"sportId": sport_id, "isLive": 1, "oddsFormat": "Decimal"})

        # Sequential fetch to ensure odds are consistent with fixtures metadata
        # (Using gather might occasionally lead to slight sync issues if one takes much longer)
        fixtures = await _fetch_fixtures()
        if not fixtures:
            return []
        data = await _fetch_odds()
        if not fixtures:
            return []

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
                        "max_moneyline": period.get("maxMoneyline") or 0,
                        "max_spread":    period.get("maxSpread")    or 0,
                        "max_total":     period.get("maxTotal")     or 0,
                    }
                    if p["moneyline"] or p["spreads"]:
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
