"""
Arb execution engine.

Flow (Kalshi FIRST, then PS3838):
  1. Place Kalshi IOC limit order at yes_ask / no_ask price
  2. Wait up to fill_wait_sec for fill confirmation
  3. Check actual filled contracts (partial fill is common)
  4. If filled >= min_fill_pct (50%) → proceed to PS3838
  5. Place PS3838 bet sized to the ACTUAL Kalshi fill (not planned)
  6. Return ExecutionResult with full details

If Kalshi fills less than min_fill_pct, cancel remainder and abort PS3838.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .arb_detector import ArbOpportunity
from .stake_calc import Stakes, kalshi_taker_fee

logger = logging.getLogger(__name__)

FILL_WAIT_SEC = 3
MIN_FILL_PCT  = 0.5


@dataclass
class LegResult:
    platform: str
    requested: float
    filled: float
    fill_pct: float
    avg_price: float
    cost: float
    order_id: Optional[str]
    status: str              # "filled" | "partial" | "unfilled" | "rejected" | "error"
    raw: dict = field(default_factory=dict)


@dataclass
class ExecutionResult:
    arb_id: str
    event_name: str
    kalshi: LegResult
    ps3838: Optional[LegResult]
    aborted: bool
    abort_reason: Optional[str]
    actual_kalshi_cost: float
    actual_ps_stake: float
    estimated_net_profit: float
    estimated_profit_pct: float


def _kalshi_fill_summary(order: dict) -> tuple:
    status = order.get("status", "unknown")
    fills  = order.get("fills", []) or []

    if not fills:
        filled = order.get("filled_count") or order.get("remainingCount") and \
                 (order.get("count", 0) - order.get("remainingCount", 0)) or 0
        avg_price = order.get("yes_price") or order.get("no_price") or 0
    else:
        filled = sum(f.get("count", 0) for f in fills)
        prices = [f.get("yes_price") or f.get("no_price") or 0 for f in fills]
        counts = [f.get("count", 0) for f in fills]
        avg_price = (sum(p * c for p, c in zip(prices, counts)) / filled) if filled else 0

    if status in ("filled",):
        status_str = "filled"
    elif status in ("partially_filled", "resting") and filled > 0:
        status_str = "partial"
    elif status in ("cancelled", "canceled"):
        status_str = "unfilled" if filled == 0 else "partial"
    else:
        status_str = "unfilled" if filled == 0 else "partial"

    return int(filled), float(avg_price), status_str


async def execute_arb(
    arb: ArbOpportunity,
    stakes: Stakes,
    kalshi_client,
    ps_client,
    poly_client = None,
    fill_wait_sec: int = FILL_WAIT_SEC,
    min_fill_pct: float = MIN_FILL_PCT,
    dry_run: bool = False,
) -> ExecutionResult:
    arb_id = str(uuid.uuid4())[:8]
    logger.info(
        "[%s] Executing arb: %s | K %s %s @ %.0f¢ × %d | PS %s @ %.3f",
        arb_id, arb.event_name,
        arb.kalshi_ticker, arb.kalshi_side.upper(), arb.kalshi_price * 100,
        stakes.kalshi_contracts, arb.ps3838_outcome.upper(), arb.ps3838_odds,
    )

    price_cents = round(arb.kalshi_price * 100)

    # ── LEG 1: Execution ─────────────────────────────────────────────────────
    # Use appropriate client based on ticker/platform
    is_poly = len(str(arb.kalshi_ticker)) > 60 or "0x" in str(arb.kalshi_ticker)
    platform = "polymarket" if is_poly else "kalshi"

    if dry_run:
        kalshi_leg = LegResult(
            platform=platform, requested=stakes.kalshi_contracts,
            filled=stakes.kalshi_contracts, fill_pct=1.0,
            avg_price=arb.kalshi_price, cost=stakes.kalshi_cost,
            order_id="DRY-RUN", status="filled",
        )
    else:
        try:
            if is_poly:
                # Placeholder for Poly placement (requires EIP-712)
                status_str = "unfilled"
                order = {"status": "POLY_LIVE_UNSUPPORTED"}
                logger.error("[%s] Polymarket LIVE execution not implemented", arb_id)
            else:
                order = await kalshi_client.place_order(
                    ticker=arb.kalshi_ticker, side=arb.kalshi_side,
                    price_cents=price_cents, count=stakes.kalshi_contracts,
                )
            order_id = order.get("order_id") or order.get("id")
            logger.info("[%s] Kalshi order placed: %s", arb_id, order_id)

            await asyncio.sleep(fill_wait_sec)
            order = await kalshi_client.get_order(order_id)

            filled, avg_price_cents, status_str = _kalshi_fill_summary(order)
            avg_price   = avg_price_cents / 100.0
            actual_cost = filled * avg_price

            kalshi_leg = LegResult(
                platform="kalshi", requested=stakes.kalshi_contracts,
                filled=filled,
                fill_pct=filled / stakes.kalshi_contracts if stakes.kalshi_contracts else 0,
                avg_price=avg_price, cost=round(actual_cost, 2),
                order_id=order_id, status=status_str, raw=order,
            )

            if status_str == "partial" and order.get("status") not in ("filled",):
                try:
                    await kalshi_client.cancel_order(order_id)
                    logger.info("[%s] Cancelled partial Kalshi remainder", arb_id)
                except Exception as e:
                    logger.warning("[%s] Could not cancel Kalshi remainder: %s", arb_id, e)

        except Exception as e:
            logger.error("[%s] Kalshi order failed: %s", arb_id, e)
            kalshi_leg = LegResult(
                platform="kalshi", requested=stakes.kalshi_contracts,
                filled=0, fill_pct=0, avg_price=0, cost=0,
                order_id=None, status="error", raw={"error": str(e)},
            )

    # ── Check fill threshold ─────────────────────────────────────────────────
    if kalshi_leg.filled == 0 or kalshi_leg.fill_pct < min_fill_pct:
        reason = (
            f"Kalshi не исполнился ({kalshi_leg.filled}/{stakes.kalshi_contracts} контр., "
            f"{kalshi_leg.fill_pct * 100:.0f}% < {min_fill_pct * 100:.0f}%)"
        )
        logger.warning("[%s] Aborting PS3838 leg: %s", arb_id, reason)
        return ExecutionResult(
            arb_id=arb_id, event_name=arb.event_name,
            kalshi=kalshi_leg, ps3838=None,
            aborted=True, abort_reason=reason,
            actual_kalshi_cost=kalshi_leg.cost, actual_ps_stake=0,
            estimated_net_profit=-kalshi_leg.cost, estimated_profit_pct=-100.0,
        )

    # ── Resize PS3838 stake to match actual Kalshi fill ───────────────────────
    fill_ratio        = kalshi_leg.filled / stakes.kalshi_contracts
    adjusted_ps_stake = round(stakes.ps3838_stake * fill_ratio, 2)
    if stakes.ps3838_max_stake > 0:
        adjusted_ps_stake = min(adjusted_ps_stake, stakes.ps3838_max_stake)

    logger.info(
        "[%s] Kalshi fill %.0f%% (%d/%d) → PS3838 stake $%.2f (was $%.2f)",
        arb_id, kalshi_leg.fill_pct * 100, kalshi_leg.filled, stakes.kalshi_contracts,
        adjusted_ps_stake, stakes.ps3838_stake,
    )

    # ── LEG 2: PS3838 ────────────────────────────────────────────────────────
    if dry_run:
        ps_leg = LegResult(
            platform="ps3838", requested=adjusted_ps_stake,
            filled=adjusted_ps_stake, fill_pct=1.0,
            avg_price=arb.ps3838_odds, cost=adjusted_ps_stake,
            order_id="DRY-RUN", status="filled",
        )
    else:
        try:
            # Handle Double Chance synthetic bet (placed as two separate bets)
            if arb.ps3838_outcome in ("draw_or_away", "home_or_draw"):
                # Simplified: we just place two bets. In production, we'd need more complex
                # error handling if one fails but the other succeeds.
                outcomes = ["draw", "away"] if arb.ps3838_outcome == "draw_or_away" else ["home", "draw"]
                # We need to fetch original odds to calculate individual stakes
                # For now, we use a simplified approach since we don't store individual odds in ArbOpportunity
                # This is a placeholder for actual multi-leg placement logic
                logger.warning("[%s] Double Chance execution is not fully atomic!", arb_id)
                # Fallback to single bet for now or implement multi-bet logic
                ps_status = "REJECTED"
                result = {"status": "DOUBLE_CHANCE_UNSUPPORTED_IN_LIVE", "stake": 0}
            else:
                result = await ps_client.place_bet(
                    event_id=arb.ps3838_event_id, period=arb.ps3838_period,
                    bet_type=arb.ps3838_bet_type, outcome=arb.ps3838_outcome,
                    price=arb.ps3838_odds, stake=adjusted_ps_stake, unique_id=arb_id,
                    line_id=arb.ps3838_line_id,
                    sport_id=arb.ps3838_sport_id
                )
            placed_stake = result.get("stake", 0) or 0
            placed_price = result.get("price", arb.ps3838_odds)
            ps_status    = result.get("status", "UNKNOWN")

            if ps_status == "ACCEPTED":
                status_str = "filled"
                fill_pct   = placed_stake / adjusted_ps_stake if adjusted_ps_stake else 0
            elif ps_status in ("PENDING", "PENDING_ACCEPTANCE"):
                status_str = "partial"
                fill_pct   = placed_stake / adjusted_ps_stake if adjusted_ps_stake else 0
            else:
                status_str = "rejected"
                fill_pct   = 0

            ps_leg = LegResult(
                platform="ps3838", requested=adjusted_ps_stake,
                filled=placed_stake, fill_pct=fill_pct,
                avg_price=placed_price, cost=placed_stake,
                order_id=str(result.get("bet_id", "")),
                status=status_str, raw=result,
            )
        except Exception as e:
            logger.error("[%s] PS3838 bet failed: %s", arb_id, e)
            ps_leg = LegResult(
                platform="ps3838", requested=adjusted_ps_stake,
                filled=0, fill_pct=0, avg_price=arb.ps3838_odds, cost=0,
                order_id=None, status="error", raw={"error": str(e)},
            )

    # ── Net P&L estimate ─────────────────────────────────────────────────────
    k_gross_payout = kalshi_leg.filled * 1.0
    if platform == "polymarket":
        from .stake_calc import polymarket_taker_fee
        k_fee = polymarket_taker_fee(int(kalshi_leg.filled), kalshi_leg.avg_price)
    else:
        k_fee = kalshi_taker_fee(int(kalshi_leg.filled), kalshi_leg.avg_price)

    k_net_payout   = k_gross_payout - k_fee
    ps_payout      = ps_leg.filled * arb.ps3838_odds

    total_spent = kalshi_leg.cost + ps_leg.cost
    min_payout  = min(k_net_payout, ps_payout) if ps_leg.filled > 0 else k_net_payout
    net_profit  = min_payout - total_spent
    profit_pct  = (net_profit / total_spent * 100) if total_spent > 0 else 0

    logger.info(
        "[%s] Done. Spent $%.2f | Net profit est. $%.2f (%.2f%%) | "
        "K: %s %d/%d @ %.0f¢ | PS: %s $%.2f/$%.2f @ %.3f",
        arb_id, total_spent, net_profit, profit_pct,
        kalshi_leg.status, kalshi_leg.filled, stakes.kalshi_contracts,
        kalshi_leg.avg_price * 100,
        ps_leg.status, ps_leg.filled, adjusted_ps_stake, ps_leg.avg_price,
    )

    return ExecutionResult(
        arb_id=arb_id, event_name=arb.event_name,
        kalshi=kalshi_leg, ps3838=ps_leg,
        aborted=False, abort_reason=None,
        actual_kalshi_cost=kalshi_leg.cost,
        actual_ps_stake=ps_leg.cost,
        estimated_net_profit=round(net_profit, 2),
        estimated_profit_pct=round(profit_pct, 3),
    )
