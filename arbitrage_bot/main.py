"""
Arbitrage bot: PS3838 (live) ↔ Kalshi

Execution order:
  1. Kalshi order first
  2. PS3838 bet second (only after Kalshi confirmed)

Rules:
  • Kalshi total_over markets: YES only (= OVER)
  • Soccer total markets: only checked when PS3838 signals halftime
  • Min arb profit: 0.5%
  • DRY_RUN=true (default)
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional
import aiohttp

from .config import PS3838_POLL_INTERVAL, DRY_RUN, BANKROLL, MAX_LEG_STAKE
from .connectors.ps3838 import PS3838Client
from .connectors.kalshi import KalshiClient
from .connectors.polymarket import PolymarketClient
from .engine.matcher import find_best_kalshi_match, ABBREV_MAP
from .engine.arb_detector import detect_arb
from .engine.stake_calc import calculate_stakes
from .engine.executor import execute_arb
from .engine.notifier import TelegramNotifier
from .bot_engine import scan_once

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("arb_bot")

KALSHI_REFRESH_EVERY = 10 # More frequent market refresh


async def main():
    mode = "DRY-RUN" if DRY_RUN else "LIVE"
    logger.info("Арб-бот запущен [%s] | банкролл $%.0f", mode, BANKROLL)

    notifier = TelegramNotifier()
    await notifier.notify_startup(mode, BANKROLL)

    async with aiohttp.ClientSession() as session:
        ps     = PS3838Client(session)
        kalshi = KalshiClient(session)
        poly   = PolymarketClient(session)

        try:
            ps_bal = await ps.get_balance()
            k_bal  = await kalshi.get_balance()
            p_bal  = await poly.get_balance()
            logger.info("PS3838 $%.2f | Kalshi $%.2f | Poly $%.2f",
                        ps_bal.get("availableBalance", 0),
                        k_bal.get("balance_usd", 0),
                        p_bal.get("balance_usd", 0))
        except Exception as e:
            logger.error("Could not fetch balances: %s", e)

        logger.info("Загружаю рынки Kalshi и Polymarket…")
        kalshi_events = await kalshi.get_all_sports_events()

        poly_events = []
        try:
            poly_resp = await poly.get_markets()
            poly_events = poly_resp.get("data", []) if isinstance(poly_resp, dict) else []
        except Exception as e:
            logger.warning("Polymarket load error: %s", e)

        iteration = 0
        while True:
            loop_start = asyncio.get_event_loop().time()
            if iteration > 0 and iteration % KALSHI_REFRESH_EVERY == 0:
                try:
                    kalshi_events = await kalshi.get_all_sports_events()
                    poly_resp = await poly.get_markets()
                    poly_events = poly_resp.get("data", []) if isinstance(poly_resp, dict) else []
                except Exception as e:
                    logger.warning("Market refresh error: %s", e)
            try:
                opps = await scan_once(
                    ps, kalshi_events, poly_events=poly_events,
                    kalshi_client=kalshi, poly_client=poly
                )

                elapsed = (asyncio.get_event_loop().time() - loop_start)
                if iteration % 5 == 0:
                    logger.info("Сканирование активно (цикл %.2fs). PS3838 Live Events OK", elapsed)

                if opps:
                    for arb, stakes, ke, k_market in opps:
                        if stakes.kalshi_contracts <= 0:
                            continue
                        result = await execute_arb(
                            arb=arb, stakes=stakes,
                            kalshi_client=kalshi, ps_client=ps,
                            poly_client=poly,
                            dry_run=DRY_RUN,
                        )

                        await notifier.notify_arb(result)

                        logger.info(
                            "ARB [%s] spent=$%.2f profit_est=$%.2f (%.2f%%) | %s | PS Odds: %.3f, K Price: %.0f¢",
                            result.arb_id,
                            result.actual_kalshi_cost + (result.ps3838.cost if result.ps3838 else 0),
                            result.estimated_net_profit,
                            result.estimated_profit_pct,
                            result.event_name,
                            arb.ps3838_odds,
                            arb.kalshi_price * 100,
                        )
            except Exception as e:
                logger.error("Scan error: %s", e, exc_info=True)

            iteration += 1
            await asyncio.sleep(PS3838_POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
