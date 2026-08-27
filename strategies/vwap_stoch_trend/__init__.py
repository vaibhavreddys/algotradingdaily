"""
strategies/vwap_stoch_trend
Bi-Directional Intraday Trend & Momentum Engine (Long + Short).
"""

from .v1_0 import (
    STRATEGY_NAME,
    STRATEGY_VERSION,
    TIMEFRAME,
    SWING_BARS,
    STRATEGY_INSTANCE,
    VWAPStochTrendStrategy,
    evaluate_signals,
    simulate_single_trade,
    calculate_stop_and_target,
    calculate_trailing_stop,
)

__all__ = [
    "STRATEGY_NAME",
    "STRATEGY_VERSION",
    "TIMEFRAME",
    "SWING_BARS",
    "STRATEGY_INSTANCE",
    "VWAPStochTrendStrategy",
    "evaluate_signals",
    "simulate_single_trade",
    "calculate_stop_and_target",
    "calculate_trailing_stop",
]
