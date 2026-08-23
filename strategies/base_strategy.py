"""
strategies/base_strategy.py
Abstract Base Strategy & Contract Interface.

Enforces a standardized contract for all quantitative trading strategies:
  - Identity & Timing Metadata (name, version, timeframe, warmup/cutoff minutes)
  - Mandatory Strategy Square-Off (SQUAREOFF_MINUTES_BEFORE_CLOSE)
  - Mandatory Signal Generation (evaluate_signals)
  - Mandatory Trade Exits (calculate_stop_and_target)
  - Optional Trailing Stop Loss rules (calculate_trailing_stop)
"""

import datetime
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Dict, Any
import pandas as pd
from core.market_calendar import (
    get_market_open_close,
    is_continuous_market,
    get_platform_hard_squareoff_time,
)


class BaseStrategy(ABC):
    """
    Standardized Quantitative Strategy Interface.
    Every strategy must encapsulate its indicators, entry rules, and exit formulas.
    """
    NAME: str = "BaseStrategy"
    VERSION: str = "1.0.0"
    TIMEFRAME: str = "15m"                           # MANDATORY: e.g. "1m", "5m", "15m", "1h", "1d"
    ENTRY_WARMUP_MINUTES_AFTER_OPEN: int = 45        # MANDATORY: e.g. 45m after open
    ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE: int = 120     # MANDATORY: e.g. 2h before close
    SQUAREOFF_MINUTES_BEFORE_CLOSE: int = 30         # MANDATORY: e.g. 30m before close (15:00 on NSE)

    def get_strategy_squareoff_time(self, market_key: str = "NSE") -> Optional[datetime.time]:
        """
        Calculates the strategy-defined auto-squareoff time for the target exchange:
        squareoff_time = market_close - SQUAREOFF_MINUTES_BEFORE_CLOSE
        Returns None for 24/7 continuous crypto markets.
        """
        if is_continuous_market(market_key):
            return None
        _, close_time = get_market_open_close(market_key)
        if close_time is None:
            return None
        dummy_date = datetime.date(2026, 1, 1)
        sq_dt = datetime.datetime.combine(dummy_date, close_time) - datetime.timedelta(minutes=self.SQUAREOFF_MINUTES_BEFORE_CLOSE)
        return sq_dt.time()

    def get_effective_squareoff_time(self, market_key: str = "NSE") -> Optional[datetime.time]:
        """
        Returns min(Strategy Squareoff, Platform Hard Cutoff 15m before close) as a fail-safe.
        """
        strat_sq = self.get_strategy_squareoff_time(market_key)
        platform_sq = get_platform_hard_squareoff_time(market_key)

        if strat_sq is None:
            return platform_sq
        if platform_sq is None:
            return strat_sq
        return min(strat_sq, platform_sq)

    @abstractmethod
    def evaluate_signals(
        self, 
        df: pd.DataFrame, 
        benchmark_pct_map: Optional[pd.Series] = None
    ) -> Optional[pd.DataFrame]:
        """
        Evaluates indicator criteria and enriches DataFrame with boolean 'Signal' column.
        Returns enriched DataFrame or None if insufficient data.
        """
        pass

    @abstractmethod
    def calculate_stop_and_target(
        self, 
        df: pd.DataFrame, 
        entry_idx: int
    ) -> Tuple[float, float, float]:
        """
        MANDATORY CONTRACT:
        Calculates and returns (sl_price, target_price, absolute_risk) for a trade entry.
        """
        pass

    def calculate_trailing_stop(
        self, 
        entry_price: float, 
        current_sl: float, 
        ltp: float, 
        high: float, 
        low: float, 
        initial_risk: float
    ) -> Optional[float]:
        """
        OPTIONAL: Evaluates dynamic trailing stop-loss rules.
        Default Implementation: Standard +1R profit moves SL to Breakeven (entry_price).
        Returns new SL price if trailing triggered, else None.
        """
        if current_sl > entry_price and low <= (entry_price - initial_risk):
            return entry_price
        return None
