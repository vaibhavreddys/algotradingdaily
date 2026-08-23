"""
strategies/vwap_stoch_breakdown
Package entrypoint for the VWAP-Stochastic RSI Breakdown strategy family.

Exports the active stable production version (default: v1_0).
"""

from .v1_0 import (
    VWAPStochBreakdownStrategy,
    STRATEGY_INSTANCE,
    STRATEGY_NAME,
    STRATEGY_VERSION,
    TIMEFRAME,
    SWING_HIGH_BARS,
    MIN_SL_BUFFER_PCT,
    SWING_SL_BUFFER_PCT,
    RISK_REWARD_RATIO,
    evaluate_signals,
    calculate_stop_and_target,
    simulate_single_trade,
)

__all__ = [
    "VWAPStochBreakdownStrategy",
    "STRATEGY_INSTANCE",
    "STRATEGY_NAME",
    "STRATEGY_VERSION",
    "TIMEFRAME",
    "SWING_HIGH_BARS",
    "MIN_SL_BUFFER_PCT",
    "SWING_SL_BUFFER_PCT",
    "RISK_REWARD_RATIO",
    "evaluate_signals",
    "calculate_stop_and_target",
    "simulate_single_trade",
]
