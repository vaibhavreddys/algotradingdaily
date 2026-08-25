"""
core/risk.py
Account-Level Capital Management, Position Sizing, and Circuit Breakers.

Provides platform-wide risk controls:
  - Dual-Guard Position Sizing: min(Margin Capacity, Fixed 1% Risk Allocation)
  - Daily Loss Circuit Breaker: Halts trading if cumulative daily loss >= 4%
  - Universal trailing stop-loss decision helper
"""

import math
from typing import Optional


def calculate_risk_based_quantity(
    entry_price: float,
    sl_price: float,
    current_capital: float,
    max_risk_pct: float = 0.01,
    max_exposure: Optional[float] = None
) -> int:
    """
    Dual-Guard Position Sizing (Issue #24):
    1. Risk-Based Quantity: Floor( (Capital * max_risk_pct) / Abs(Entry - SL) )
    2. Exposure-Based Quantity: Floor( max_exposure / Entry )
    Returns min(Risk-Based Qty, Exposure-Based Qty), minimum 1 share if capital permits.
    """
    if entry_price <= 0 or sl_price <= 0 or current_capital <= 0:
        return 0

    per_share_risk = abs(entry_price - sl_price)
    if per_share_risk <= 0:
        if max_exposure and max_exposure >= entry_price:
            return max(1, int(math.floor(max_exposure / entry_price)))
        return 0

    capital_at_risk = current_capital * max_risk_pct
    risk_qty = int(math.floor(capital_at_risk / per_share_risk))

    if max_exposure is not None and max_exposure > 0:
        margin_qty = int(math.floor(max_exposure / entry_price))
        final_qty = min(risk_qty, margin_qty)
    else:
        final_qty = risk_qty

    return max(1, final_qty) if (max_exposure is None or max_exposure >= entry_price) else 0


def is_daily_loss_limit_reached(
    today_realized_pnl: Optional[float] = None,
    day_starting_capital: Optional[float] = None,
    max_loss_pct: float = 0.04,
    starting_capital: Optional[float] = None,
    current_capital: Optional[float] = None,
    **kwargs
) -> bool:
    start_cap = day_starting_capital if day_starting_capital is not None else (starting_capital or 0.0)
    if start_cap <= 0:
        return True

    if today_realized_pnl is None and current_capital is not None:
        pnl = current_capital - start_cap
    else:
        pnl = today_realized_pnl if today_realized_pnl is not None else 0.0

    daily_loss_limit = -abs(start_cap * max_loss_pct)
    return pnl <= daily_loss_limit


def should_trail_to_breakeven(
    entry_price: float,
    current_ltp: float,
    initial_sl: float,
    current_sl: float
) -> bool:
    """
    Evaluates whether a trade has achieved +1R profit to move Stop Loss to Breakeven (Entry Price).
    For a SHORT position: +1R is achieved when current_ltp <= entry_price - initial_risk.
    """
    if initial_sl <= entry_price or current_sl <= entry_price:
        return False

    initial_risk = initial_sl - entry_price
    one_r_target = entry_price - initial_risk
    return current_ltp <= one_r_target
