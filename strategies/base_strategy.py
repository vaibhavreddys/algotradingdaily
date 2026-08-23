"""
strategies/base_strategy.py
Abstract Base Strategy & Contract Interface.

Enforces a standardized contract for all quantitative trading strategies:
  - Identity & Timing Metadata (name, version, warmup/cutoff minutes)
  - Mandatory Signal Generation (evaluate_signals)
  - Mandatory Trade Exits (calculate_stop_and_target)
  - Optional Trailing Stop Loss rules (calculate_trailing_stop)
"""

from abc import ABC, abstractmethod
from typing import Optional, Tuple, Dict, Any
import pandas as pd


class BaseStrategy(ABC):
    """
    Standardized Quantitative Strategy Interface.
    Every strategy must encapsulate its indicators, entry rules, and exit formulas.
    """
    NAME: str = "BaseStrategy"
    VERSION: str = "1.0.0"
    TIMEFRAME: str = "15m"  # MANDATORY: e.g. "1m", "5m", "15m", "1h", "1d"
    WARMUP_MINUTES: int = 45
    CUTOFF_MINUTES: int = 120

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
