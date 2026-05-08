"""
Stake calculator with exchange-specific taker fee formulas.

Kalshi (taker):     fee = ceil(0.07 × C × P × (1-P))
Polymarket (taker): fee = 0.02 × C × P   (2% of cost)

Bankroll equation:
  S = n × (imp_ps + P) + fee(n)
  → n ≈ S / (imp_ps + P + fee_per_unit)
"""

import math
from dataclasses import dataclass, field
from typing import Optional

from .arb_detector import ArbOpportunity


def kalshi_taker_fee(contracts: int, price: float) -> float:
    """Kalshi taker fee: ceil(0.07 × C × P × (1-P)), rounded up to cent."""
    return math.ceil(0.07 * contracts * price * (1.0 - price) * 100) / 100.0


def polymarket_taker_fee(contracts: int, price: float) -> float:
    """Polymarket taker fee: 2% of cost (0.02 × C × P), rounded up to cent."""
    return math.ceil(0.02 * contracts * price * 100) / 100.0


@dataclass
class Stakes:
    ps3838_stake: float
    ps3838_max_stake: float
    ps3838_stake_capped: bool
    kalshi_contracts: int
    kalshi_cost: float
    kalshi_fee: float
    kalshi_total_cost: float
    kalshi_net_payout: float
    total_spent: float
    guaranteed_profit: float
    guaranteed_profit_gross: float
    profit_pct: float
    profit_pct_gross: float
    limit_warning: Optional[str] = field(default=None)


def calculate_stakes(
    arb: ArbOpportunity,
    total_bankroll: float,
    max_ps_stake: float = 0.0,
    global_max_stake: float = 0.0,
    fee_fn=None,
) -> Stakes:
    P      = arb.kalshi_price
    imp_ps = arb.implied_ps
    if fee_fn is None:
        fee_fn = kalshi_taker_fee
    k_coef = 0.07 if fee_fn is kalshi_taker_fee else 0.02

    denom = imp_ps + P + k_coef * P * (1.0 - P)
    n     = int(total_bankroll / denom) if denom > 0 else 0

    while n > 0:
        fee   = fee_fn(n, P)
        sp    = n * imp_ps
        total = sp + n * P + fee
        if total <= total_bankroll:
            break
        n -= 1

    if n > 0 and global_max_stake > 0:
        max_n_global = max(int(global_max_stake / P), 0) if P > 0 else n
        if n > max_n_global:
            n = max_n_global

    capped  = False
    warning = None
    if n > 0 and max_ps_stake > 0:
        stake_ps_uncapped = n * imp_ps
        if stake_ps_uncapped > max_ps_stake:
            capped  = True
            ratio   = max_ps_stake / stake_ps_uncapped
            n       = max(int(n * ratio), 0)
            warning = f"PS3838 max bet ${max_ps_stake:.0f} — ставка уменьшена до лимита"

    if n <= 0:
        return Stakes(
            ps3838_stake=0, ps3838_max_stake=max_ps_stake, ps3838_stake_capped=capped,
            kalshi_contracts=0, kalshi_cost=0, kalshi_fee=0, kalshi_total_cost=0,
            kalshi_net_payout=0, total_spent=0,
            guaranteed_profit=0, guaranteed_profit_gross=0,
            profit_pct=0, profit_pct_gross=0, limit_warning=warning,
        )

    fee          = fee_fn(n, P)
    stake_ps     = round(n * imp_ps, 2)
    kalshi_cost  = round(n * P, 2)
    kalshi_total = round(kalshi_cost + fee, 2)
    total_spent  = round(stake_ps + kalshi_total, 2)
    net_payout   = float(n)

    profit_gross = round(net_payout - (stake_ps + kalshi_cost), 2)
    pct_gross    = round(profit_gross / (stake_ps + kalshi_cost) * 100, 3) \
                   if (stake_ps + kalshi_cost) > 0 else 0.0
    profit_net   = round(net_payout - total_spent, 2)
    pct_net      = round(profit_net / total_spent * 100, 3) if total_spent > 0 else 0.0

    return Stakes(
        ps3838_stake=stake_ps,
        ps3838_max_stake=round(max_ps_stake, 2),
        ps3838_stake_capped=capped,
        kalshi_contracts=n,
        kalshi_cost=kalshi_cost,
        kalshi_fee=round(fee, 2),
        kalshi_total_cost=kalshi_total,
        kalshi_net_payout=net_payout,
        total_spent=total_spent,
        guaranteed_profit=profit_net,
        guaranteed_profit_gross=profit_gross,
        profit_pct=pct_net,
        profit_pct_gross=pct_gross,
        limit_warning=warning,
    )
