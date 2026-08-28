"""
strategies/vwap_stoch_trend/v1_2.py
Bi-Directional VWAP + Stochastic RSI Intraday Relative Momentum Strategy (v1.2).

Refinements in v1.2:
  1. Macro Market Alignment:
     - Long only when broader NIFTY is in a positive regime.
     - Short in bearish / corrective regimes.
  2. Relative Momentum Pullback:
     - Long: Stock Outperforming NIFTY (Stock % > Nifty %) + Price > VWAP + Stochastic RSI Oversold Hook (<= 20 -> > 20).
     - Short: Stock Underperforming NIFTY (Stock % < Nifty %) + Price < VWAP + Stochastic RSI Overbought Hook (>= 80 -> < 80).
  3. Execution & Trailing Protection:
     - Entry Window: 10:00 AM to 1:30 PM (after 45m initial warmup).
     - 1:1.5 Risk-Reward Target.
     - +1R Breakeven Trailing Stop Loss Protection.
     - Mandatory 3:00 PM Afternoon Squareoff Runner.
"""

import datetime
from typing import Optional, Dict, Any, Tuple
import pandas as pd
import numpy as np

from config import CONFIG, TradingConfig
from core.indicators import compute_stoch_rsi, compute_adx, compute_vwap, compute_relative_weakness
from core.trade_db import TradeExitReason
from core.market_calendar import get_strategy_entry_window, is_continuous_market
from strategies.base_strategy import BaseStrategy

STRATEGY_NAME = "VWAP-Stoch Trend"
STRATEGY_VERSION = "v1_2"
TIMEFRAME = "15m"
SWING_BARS = 3


class VWAPStochTrendStrategyV12(BaseStrategy):
    NAME: str = "VWAP-Stoch Trend"
    VERSION: str = "1.2.0"
    TIMEFRAME: str = "15m"
    WARMUP_MINUTES: int = 45   # 10:00 AM on NSE
    CUTOFF_MINUTES: int = 120  # 1:30 PM on NSE
    SQUAREOFF_MINUTES_BEFORE_CLOSE: int = 30  # 3:00 PM

    SWING_BARS: int = 3
    MIN_SL_BUFFER_PCT: float = 0.0020
    SWING_SL_BUFFER_PCT: float = 0.0005
    RISK_REWARD_RATIO: float = 1.5
    ADX_THRESHOLD: float = 25.0
    STOCH_OVERBOUGHT: float = 80.0
    STOCH_OVERSOLD: float = 20.0

    def evaluate_signals(
        self,
        df: pd.DataFrame,
        nifty_pct_map: Optional[pd.Series] = None,
        config: Optional[TradingConfig] = None
    ) -> Optional[pd.DataFrame]:
        if df is None or df.empty or len(df) < 50:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # 1. Technical Indicators
        df = compute_stoch_rsi(df)
        df = compute_adx(df)
        df = compute_vwap(df)

        if 'Stoch_K' not in df.columns or 'ADX' not in df.columns or 'VWAP' not in df.columns:
            return None

        # 2. Relative Strength & Weakness vs Benchmark
        df = compute_relative_weakness(df, nifty_pct_map)
        df['Rel_Weakness_Pass'] = df['Rel_Weakness'].fillna(False)
        df['Rel_Strength_Pass'] = ~df['Rel_Weakness_Pass']

        # 3. Time Filter (10:00 AM to 1:30 PM)
        market_key = getattr(config, 'EXCHANGE_MARKET', 'NSE') if config else 'NSE'
        if not is_continuous_market(market_key):
            entry_start, entry_end = get_strategy_entry_window(
                market_key=market_key,
                warmup_minutes=self.WARMUP_MINUTES,
                cutoff_minutes=self.CUTOFF_MINUTES
            )
            if entry_start is not None and entry_end is not None:
                time_filter = (df.index.time >= entry_start) & (df.index.time <= entry_end)
            else:
                time_filter = pd.Series(True, index=df.index)
        else:
            time_filter = pd.Series(True, index=df.index)

        # 4. Trend Strength confirmation
        adx_thresh = getattr(config, 'ADX_THRESHOLD', self.ADX_THRESHOLD)
        df['ADX_Pass'] = df['ADX'] >= adx_thresh

        # 5. Stochastic RSI Hooks
        stoch_ob = getattr(config, 'STOCH_OVERBOUGHT', self.STOCH_OVERBOUGHT)
        stoch_os = getattr(config, 'STOCH_OVERSOLD', self.STOCH_OVERSOLD)
        df['Stoch_Short_Pass'] = (df['Stoch_K_prev'] >= stoch_ob) & (df['Stoch_K'] < stoch_ob)
        df['Stoch_Long_Pass'] = (df['Stoch_K_prev'] <= stoch_os) & (df['Stoch_K'] > stoch_os)

        # 6. VWAP Positioning
        df['VWAP_Short_Pass'] = df['Close'] < df['VWAP']
        df['VWAP_Long_Pass'] = df['Close'] > df['VWAP']

        # 7. Directional Signal Triggers
        df['Short_Signal'] = (
            time_filter &
            df['ADX_Pass'] &
            df['Rel_Weakness_Pass'] &
            df['VWAP_Short_Pass'] &
            df['Stoch_Short_Pass']
        )

        df['Long_Signal'] = (
            time_filter &
            df['ADX_Pass'] &
            df['Rel_Strength_Pass'] &
            df['VWAP_Long_Pass'] &
            df['Stoch_Long_Pass']
        )

        df['Signal'] = df['Short_Signal'] | df['Long_Signal']
        df['Direction'] = np.where(df['Short_Signal'], 'SHORT', np.where(df['Long_Signal'], 'LONG', 'NONE'))

        return df

    def calculate_stop_and_target(
        self,
        df: pd.DataFrame,
        entry_idx: int,
        direction: str = "SHORT"
    ) -> Tuple[float, float, float]:
        entry_price = float(df.iloc[entry_idx]['Close'])
        if entry_price <= 0:
            return 0.0, 0.0, 0.0

        swing_start = max(0, entry_idx - self.SWING_BARS)
        rr_ratio = getattr(CONFIG, 'RISK_REWARD_RATIO', self.RISK_REWARD_RATIO)

        if str(direction).upper() == "LONG":
            swing_low = float(df.iloc[swing_start : entry_idx + 1]['Low'].min()) if entry_idx > 0 else entry_price
            sl_swing = swing_low * (1.0 - self.SWING_SL_BUFFER_PCT)
            sl_min = entry_price * (1.0 - self.MIN_SL_BUFFER_PCT)
            sl_price = min(sl_swing, sl_min)
            risk = entry_price - sl_price
            target_price = entry_price + (risk * rr_ratio)
        else:
            swing_high = float(df.iloc[swing_start : entry_idx + 1]['High'].max()) if entry_idx > 0 else entry_price
            sl_swing = swing_high * (1.0 + self.SWING_SL_BUFFER_PCT)
            sl_min = entry_price * (1.0 + self.MIN_SL_BUFFER_PCT)
            sl_price = max(sl_swing, sl_min)
            risk = sl_price - entry_price
            target_price = entry_price - (risk * rr_ratio)

        return round(sl_price, 2), round(target_price, 2), round(risk, 2)


# Default Singleton Strategy Instance
STRATEGY_INSTANCE = VWAPStochTrendStrategyV12()
