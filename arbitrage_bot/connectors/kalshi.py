import asyncio
import time
import base64
import logging
import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from ..config import KALSHI_KEY_ID, KALSHI_PRIVATE_KEY_PATH

logger = logging.getLogger(__name__)

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
_CONCURRENCY = 30

# (series_ticker, market_type, is_soccer, is_cup)
CURATED_SERIES: list[tuple[str, str, bool, bool]] = [
    # NBA — full game only
    ("KXNBAGAME",          "moneyline",  False, False),
    ("KXNBATOTAL",         "total_over", False, False),
    ("KXNBASPREAD",        "spread",     False, False),
    # WNBA
    ("KXWNBAGAME",         "moneyline",  False, False),
    ("KXWNBATOTAL",        "total_over", False, False),
    ("KXWNBASPREAD",       "spread",     False, False),
    # MLB — full game only (F5 excluded: different settlement scope)
    ("KXMLBGAME",          "moneyline",  False, False),
    ("KXMLBTOTAL",         "total_over", False, False),
    ("KXMLBSPREAD",        "spread",     False, False),
    # Tennis
    ("KXATPMATCH",          "moneyline", False, False),
    ("KXATPCHALLENGERMATCH","moneyline", False, False),
    ("KXWTAMATCH",          "moneyline", False, False),
    ("KXWTACHALLENGERMATCH","moneyline", False, False),
    ("KXITFMATCH",          "moneyline", False, False),
    ("KXITFWMATCH",         "moneyline", False, False),
    # Soccer — league games (no ET risk)
    ("KXEPLGAME",          "moneyline",  True,  False),
    ("KXEPLTOTAL",         "total_over", True,  False),
    ("KXEPLSPREAD",        "spread",     True,  False),
    ("KXLALIGAGAME",       "moneyline",  True,  False),
    ("KXLALIGATOTAL",      "total_over", True,  False),
    ("KXLALIGASPREAD",     "spread",     True,  False),
    ("KXSERIEAGAME",       "moneyline",  True,  False),
    ("KXSERIEATOTAL",      "total_over", True,  False),
    ("KXSERIEASPREAD",     "spread",     True,  False),
    ("KXBUNDESLIGAGAME",   "moneyline",  True,  False),
    ("KXBUNDESLIGATOTAL",  "total_over", True,  False),
    ("KXBUNDESLIGASPREAD", "spread",     True,  False),
    ("KXLIGUE1GAME",       "moneyline",  True,  False),
    ("KXLIGUE1TOTAL",      "total_over", True,  False),
    ("KXLIGUE1SPREAD",     "spread",     True,  False),
    ("KXMLSGAME",          "moneyline",  True,  False),
    ("KXMLSTOTAL",         "total_over", True,  False),
    ("KXMLSSPREAD",        "spread",     True,  False),
    ("KXEREDIVISIEGAME",   "moneyline",  True,  False),
    ("KXEREDIVISIETOTAL",  "total_over", True,  False),
    ("KXLIGAPORTUGALGAME", "moneyline",  True,  False),
    ("KXBRASILEIROGAME",   "moneyline",  True,  False),
    ("KXBRASILEIROTOTAL",  "total_over", True,  False),
    ("KXLIGAMXGAME",       "moneyline",  True,  False),
    ("KXLIGAMXTOTAL",      "total_over", True,  False),
    ("KXCANPLGAME",        "moneyline",  True,  False),
    ("KXUSLGAME",          "moneyline",  True,  False),
    ("KXSCOTTISHPREMGAME", "moneyline",  True,  False),
    ("KXSUPERLIGGAME",     "moneyline",  True,  False),
    ("KXALLSVENSKANGAME",  "moneyline",  True,  False),
    ("KXELITESERIENGAME",  "moneyline",  True,  False),
    # Soccer — cup/knockout (⚠ ET risk)
    ("KXUCLGAME",          "moneyline",  True,  True),
    ("KXUCLTOTAL",         "total_over", True,  True),
    ("KXUCLSPREAD",        "spread",     True,  True),
    ("KXUELGAME",          "moneyline",  True,  True),
    ("KXUELTOTAL",         "total_over", True,  True),
    ("KXUELSPREAD",        "spread",     True,  True),
    # Basketball — other
    ("KXEUROLEAGUEGAME",   "moneyline",  False, False),
    ("KXACBGAME",          "moneyline",  False, False),
    # Cricket
    ("KXIPLGAME",          "moneyline",  False, False),
]

_SERIES_SPORT: dict[str, str] = {
    "KXNBA": "Basketball", "KXWNBA": "Basketball",
    "KXEUROLEAGUE": "Basketball", "KXACB": "Basketball",
    "KXMLB": "Baseball",
    "KXATP": "Tennis", "KXWTA": "Tennis", "KXITF": "Tennis",
    "KXIPL": "Cricket",
}

def _series_to_sport(series_ticker: str, is_soccer: bool) -> str:
    if is_soccer:
        return "Soccer"
    for prefix, sport in _SERIES_SPORT.items():
        if series_ticker.startswith(prefix):
            return sport
    return ""


_PRIVATE_KEY = None

def _load_private_key():
    global _PRIVATE_KEY
    if _PRIVATE_KEY is None:
        with open(KALSHI_PRIVATE_KEY_PATH, "rb") as f:
            _PRIVATE_KEY = serialization.load_pem_private_key(f.read(), password=None)
    return _PRIVATE_KEY


def _auth_headers(method: str, path: str) -> dict:
    key = _load_private_key()
    ts  = str(int(time.time() * 1000))
    sig = key.sign(
        f"{ts}{method.upper()}{path}".encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY":        KALSHI_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP":  ts,
        "KALSHI-ACCESS-SIGNATURE":  base64.b64encode(sig).decode(),
        "Content-Type": "application/json",
    }


class KalshiClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self._sem   = asyncio.Semaphore(_CONCURRENCY)

    async def _post(self, path: str, body: dict) -> dict:
        headers = _auth_headers("POST", path)
        stripped = path.replace("/trade-api/v2", "")
        async with self._sem:
            async with self.session.post(
                f"{KALSHI_BASE}{stripped}", headers=headers, json=body
            ) as r:
                r.raise_for_status()
                return await r.json()

    async def _get(self, path: str, params: dict = None) -> dict:
        headers = _auth_headers("GET", path)
        stripped = path.replace("/trade-api/v2", "")
        async with self._sem:
            async with self.session.get(
                f"{KALSHI_BASE}{stripped}", headers=headers, params=params or {}
            ) as r:
                r.raise_for_status()
                return await r.json()

    async def place_order(
        self,
        ticker: str,
        side: str,           # "yes" | "no"
        price_cents: int,    # 1–99 cents
        count: int,          # contracts
    ) -> dict:
        body = {
            "action": "buy",
            "type": "limit",
            "time_in_force": "ioc",
            "ticker": ticker,
            "side": side,
            "count": count,
            f"{side}_price": price_cents,
        }
        data = await self._post("/trade-api/v2/portfolio/orders", body)
        return data.get("order", data)

    async def get_order(self, order_id: str) -> dict:
        data = await self._get(f"/trade-api/v2/portfolio/orders/{order_id}")
        return data.get("order", data)

    async def cancel_order(self, order_id: str) -> dict:
        headers = _auth_headers("DELETE", f"/trade-api/v2/portfolio/orders/{order_id}")
        async with self._sem:
            async with self.session.delete(
                f"{KALSHI_BASE}/portfolio/orders/{order_id}", headers=headers
            ) as r:
                r.raise_for_status()
                return await r.json()

    async def get_balance(self) -> dict:
        data = await self._get("/trade-api/v2/portfolio/balance")
        return {
            "balance_usd": data.get("balance", 0) / 100,
            "portfolio_value_usd": data.get("portfolio_value", 0) / 100,
        }

    async def _fetch_series_events(self, series_ticker: str) -> list[dict]:
        try:
            data = await self._get(
                "/trade-api/v2/events",
                {"series_ticker": series_ticker, "status": "open", "limit": 100},
            )
            return data.get("events", [])
        except Exception as e:
            logger.debug("Events %s: %s", series_ticker, e)
            return []

    async def _fetch_markets(self, event_ticker: str) -> list[dict]:
        try:
            data = await self._get(
                "/trade-api/v2/markets",
                {"event_ticker": event_ticker, "status": "open"},
            )
            return data.get("markets", [])
        except Exception as e:
            logger.debug("Markets %s: %s", event_ticker, e)
            return []

    @staticmethod
    def _parse_markets(raw: list, market_type: str, is_soccer: bool) -> list[dict]:
        out = []
        for m in raw:
            ticker  = m["ticker"]
            team_id = ticker.rsplit("-", 1)[-1]
            yes_bid = float(m.get("yes_bid_dollars") or 0)
            yes_ask = float(m.get("yes_ask_dollars") or 0)
            no_bid  = float(m.get("no_bid_dollars")  or 0)
            no_ask  = round(1.0 - yes_bid, 4) if yes_bid > 0 else 0.0

            if yes_ask <= 0:
                continue

            base = dict(
                ticker=ticker, team_id=team_id,
                title=m.get("title", ""), is_soccer=is_soccer,
                yes_bid=yes_bid, yes_ask=yes_ask,
                no_bid=no_bid,   no_ask=no_ask,
            )

            if market_type == "moneyline":
                # Skip prop markets: team_id must be alphabetic team abbreviation
                if team_id in ("YES", "NO", "Y", "N") or not team_id.replace("-", "").isalpha():
                    continue
                mtype = "draw" if team_id == "TIE" else "moneyline"
                out.append({**base, "market_type": mtype,
                             "kalshi_side": "yes", "entry_price": yes_ask})

            elif market_type == "total_over":
                # YES = OVER (always)
                out.append({**base, "market_type": "total_over",
                             "kalshi_side": "yes", "entry_price": yes_ask})
                # NO = UNDER — include only for soccer (halftime check in bot_engine.py)
                if is_soccer and no_ask > 0:
                    out.append({**base, "market_type": "total_under",
                                 "kalshi_side": "no", "entry_price": no_ask})

            elif market_type == "spread":
                out.append({**base, "market_type": "spread",
                             "kalshi_side": "yes", "entry_price": yes_ask})
        return out

    async def refresh_matched_prices(self, event_meta: list) -> dict:
        seen = {}
        for event_ticker, mtype, is_soccer in event_meta:
            seen.setdefault(event_ticker, (mtype, is_soccer))

        async def _one(event_ticker, mtype, is_soccer):
            try:
                raw = await self._fetch_markets(event_ticker)
                return event_ticker, self._parse_markets(raw, mtype, is_soccer)
            except Exception as e:
                logger.debug("refresh_matched_prices %s: %s", event_ticker, e)
                return event_ticker, []

        results = await asyncio.gather(*[
            _one(t, *meta) for t, meta in seen.items()
        ])
        return {ticker: mkts for ticker, mkts in results if mkts}

    async def get_all_sports_events(self) -> list[dict]:
        series_tasks = [
            self._fetch_series_events(ticker)
            for ticker, _, _, _ in CURATED_SERIES
        ]
        series_results = await asyncio.gather(*series_tasks)

        event_queue = []
        for (series_ticker, mtype, is_soccer, is_cup), events in zip(CURATED_SERIES, series_results):
            for ev in events:
                ev["_series_ticker"] = series_ticker
                event_queue.append((ev, mtype, is_soccer, is_cup))

        logger.debug("Kalshi: %d events to load markets for", len(event_queue))

        async def _load_event(ev, mtype, is_soccer, is_cup):
            raw = await self._fetch_markets(ev["event_ticker"])
            markets = self._parse_markets(raw, mtype, is_soccer)
            if not markets:
                return None
            return {
                "event_ticker": ev["event_ticker"],
                "series":       ev.get("_series_ticker", ""),
                "market_type":  mtype,
                "sport":        _series_to_sport(ev.get("_series_ticker", ""), is_soccer),
                "is_cup":       is_cup,
                "title":        ev.get("title", ""),
                "sub_title":    ev.get("sub_title", ""),
                "markets":      markets,
            }

        market_tasks = [_load_event(ev, mt, isc, icp) for ev, mt, isc, icp in event_queue]
        results = await asyncio.gather(*market_tasks)
        valid = [r for r in results if r]

        logger.info(
            "Kalshi: %d event groups ready (from %d curated series, %d raw events)",
            len(valid), len(CURATED_SERIES), len(event_queue),
        )
        return valid
