"""
Core Risk Management Module.

Provides pure mathematical helpers and guard functions for:
  - Formulating anti-wick buffered Stop Loss and 1:2 R:R Profit Target prices.
  - Formulating +1R Trailing Stop-Loss to Breakeven trigger rules.
  - Enforcing the 3% Daily Portfolio Loss Circuit Breaker across all execution engines.
"""

import math
from typing import Tuple
from config import CONFIG, TradingConfig


def calculate_stop_and_target(
    entry_price: float,
    swing_high: float,
    config: TradingConfig = CONFIG
) -> Tuple[float, float, float]:
    """
    Calculates the Initial Stop-Loss price, Profit Target price, and Risk per share.

    Formula:
      sl_price = max(swing_high * (1 + SWING_SL_BUFFER_PCT), entry_price * (1 + MIN_SL_BUFFER_PCT))
      risk = sl_price - entry_price
      target_price = entry_price - (risk * RISK_REWARD_RATIO)

    Returns:
      Tuple of (sl_price, target_price, risk) with 2-decimal rounding.
    """
    if entry_price <= 0:
        return 0.0, 0.0, 0.0

    sl_swing = swing_high * (1.0 + config.SWING_SL_BUFFER_PCT)
    sl_min = entry_price * (1.0 + config.MIN_SL_BUFFER_PCT)
    sl_price = max(sl_swing, sl_min)

    risk = sl_price - entry_price
    target_price = entry_price - (risk * config.RISK_REWARD_RATIO)

    return round(sl_price, 2), round(target_price, 2), round(risk, 2)


def is_daily_loss_limit_reached(
    today_realized_pnl: float,
    day_starting_capital: float,
    max_loss_pct: float = 0.03
) -> bool:
    """
    Evaluates whether the 3% Daily Portfolio Loss Circuit Breaker has been tripped.

    Args:
      today_realized_pnl: Net PnL of all completed trades for today in INR (negative for loss).
      day_starting_capital: Account equity balance at 09:15 AM opening in INR.
      max_loss_pct: Daily circuit breaker loss threshold (default: 0.03 or 3%).

    Returns:
      True if today's net losses meet or exceed the daily loss limit, halting all new entries.
    """
    if day_starting_capital <= 0:
        return True
    max_loss_limit = day_starting_capital * max_loss_pct
    return today_realized_pnl <= -max_loss_limit


def should_trail_to_breakeven(
    entry_price: float,
    risk: float,
    low_price: float,
    already_trailed: bool = False
) -> bool:
    """
    Evaluates whether an active Short position has reached +1R profit to trail SL to Breakeven.

    Rule:
      For Short trade: if low_price <= (entry_price - risk) and not already_trailed -> True.
    """
    if already_trailed or risk <= 0:
        return False
    return low_price <= (entry_price - risk)


def calculate_risk_based_quantity(
    entry_price: float,
    sl_price: float,
    current_capital: float,
    max_risk_pct: float = 0.01,
    max_exposure: float = 0.0
) -> int:
    """
    Calculates the integer share quantity based on Fixed Fractional Risk Sizing (Issue #24).
    Ensures maximum loss at Stop-Loss does not exceed (current_capital * max_risk_pct).
    Applies dual-guard: min(risk_quantity, margin_quantity).
    """
    if entry_price <= 0 or current_capital <= 0:
        return 0

    risk_per_share = abs(sl_price - entry_price)
    if risk_per_share <= 0:
        risk_per_share = entry_price * 0.0020  # Fallback to 0.2% min buffer

    max_risk_inr = current_capital * max_risk_pct
    risk_qty = int(math.floor(max_risk_inr / risk_per_share))

    if max_exposure > 0:
        margin_qty = int(math.floor(max_exposure / entry_price))
        final_qty = min(risk_qty, margin_qty)
    else:
        final_qty = risk_qty

    return max(1, final_qty) if (max_exposure <= 0 or max_exposure >= entry_price) else 0
