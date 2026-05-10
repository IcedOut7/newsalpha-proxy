import os
import time
import hmac
import hashlib
import base64
import logging
import aiohttp
from ..config import POLYMARKET_KEY, POLYMARKET_SECRET, POLYMARKET_PASSPHRASE

logger = logging.getLogger(__name__)

POLYMARKET_CLOB_URL = "https://clob.polymarket.com"

class PolymarketClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    def _get_auth_headers(self, method, path, body=""):
        timestamp = str(int(time.time()))
        message = f"{timestamp}{method.upper()}{path}{body}"
        signature = hmac.new(
            POLYMARKET_SECRET.encode(),
            message.encode(),
            hashlib.sha256
        ).digest()

        return {
            "POLY-API-KEY": POLYMARKET_KEY,
            "POLY-API-SIGNATURE": base64.b64encode(signature).decode(),
            "POLY-API-TIMESTAMP": timestamp,
            "POLY-API-PASSPHRASE": POLYMARKET_PASSPHRASE,
            "Content-Type": "application/json"
        }

    async def get_balance(self) -> dict:
        # For simplicity, returning a placeholder or fetching from a real endpoint if available
        # Polymarket usually uses USDC on Polygon.
        return {"balance_usd": 0}

    async def get_markets(self, next_cursor: str = "") -> dict:
        # Fetch active markets from CLOB
        path = "/markets"
        params = {"active": "true", "limit": 100}
        if next_cursor:
            params["next_cursor"] = next_cursor
        async with self.session.get(f"{POLYMARKET_CLOB_URL}{path}", params=params) as r:
            r.raise_for_status()
            return await r.json()

    async def get_orderbook(self, token_id: str) -> dict:
        path = f"/book"
        params = {"token_id": token_id}
        async with self.session.get(f"{POLYMARKET_CLOB_URL}{path}", params=params) as r:
            r.raise_for_status()
            return await r.json()
