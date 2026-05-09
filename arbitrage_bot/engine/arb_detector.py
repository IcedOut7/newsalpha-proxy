from dataclasses import dataclass
from typing import Optional
from ..config import MIN_ARB_PROFIT_PCT


@dataclass
class ArbOpportunity:
    event_name: str
    ps3838_event_id: int
    ps3838_sport_id: int
    ps3838_line_id: int
    kalshi_ticker: str
    ps3838_outcome: str      # "home" | "away" | "draw" | "over" | "under"
    ps3838_odds: float       # decimal
    ps3838_period: int
    ps3838_bet_type: str
    kalshi_side: str         # "yes" | "no"
    kalshi_price: float      # 0.0–1.0
    implied_ps: float
    implied_kalshi: float
    margin: float
    profit_pct: float


def kalshi_price_to_prob(price: float) -> float:
    return price  # already a probability


def decimal_to_prob(odds: float) -> float:
    return 1.0 / odds


def detect_arb(
    event_name: str,
    ps3838_event_id: int,
    ps3838_sport_id: int,
    ps3838_line_id: int,
    ps3838_outcome: str,
    ps3838_odds: float,
    ps3838_period: int,
    ps3838_bet_type: str,
    kalshi_ticker: str,
    kalshi_side: str,
    kalshi_price: float,
) -> Optional[ArbOpportunity]:
    """
    Check if PS3838 leg + Kalshi leg form an arbitrage.
    For arb: implied_ps + implied_kalshi < 1.0
    """
    implied_ps = decimal_to_prob(ps3838_odds)
    implied_kalshi = kalshi_price_to_prob(kalshi_price)
    margin = implied_ps + implied_kalshi
    profit_pct = (1.0 / margin - 1.0) * 100.0

    # Each leg must have meaningful probability
    if implied_ps < 0.05 or implied_kalshi < 0.05:
        return None
    # margin < 0.85 (profit > ~17.6%) almost certainly means:
    # soccer 3-way market (draw not covered), stale Kalshi price, or wrong side
    if margin < 0.85:
        return None

    if profit_pct >= MIN_ARB_PROFIT_PCT:
        return ArbOpportunity(
            event_name=event_name,
            ps3838_event_id=ps3838_event_id,
            ps3838_sport_id=ps3838_sport_id,
            ps3838_line_id=ps3838_line_id,
            kalshi_ticker=kalshi_ticker,
            ps3838_outcome=ps3838_outcome,
            ps3838_odds=ps3838_odds,
            ps3838_period=ps3838_period,
            ps3838_bet_type=ps3838_bet_type,
            kalshi_side=kalshi_side,
            kalshi_price=kalshi_price,
            implied_ps=implied_ps,
            implied_kalshi=implied_kalshi,
            margin=margin,
            profit_pct=profit_pct,
        )
    return None
