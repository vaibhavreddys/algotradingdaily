"""
VWAP-Stochastic RSI Breakdown Strategy.

Quantitative Intraday Short Strategy:
  - Timeframe: 15-minute candles
  - Entry Window: Configured via market calendar warmup/cutoff offsets (10:00 to 13:30 on NSE)
  - Core Indicators: VWAP, Stochastic RSI, ADX(14)
  - Macro Filter: NIFTY 50 Relative Weakness
  - Stop-Loss: 3-bar Swing High + 0.05% anti-wick buffer (min 0.2% above entry)
  - Profit Target: 1:2 Risk-Reward Ratio
  - Trailing Stop: +1R profit moves SL to Breakeven
"""

import datetime
from typing import Optional, Dict, Any, Tuple
import pandas as pd
import numpy as np

from config import CONFIG, TradingConfig
from core.indicators import compute_stoch_rsi, compute_adx, compute_vwap, compute_relative_weakness
from core.trade_db import TradeExitReason
from core.market_calendar import get_strategy_entry_window, get_squareoff_time, is_continuous_market
from strategies.base_strategy import BaseStrategy


class VWAPStochBreakdownStrategy(BaseStrategy):
    """
    VWAP-Stoch Breakdown Implementation conforming to BaseStrategy contract.
    """
    NAME: str = "VWAP-Stoch Breakdown"
    VERSION: str = "1.0.0"
    TIMEFRAME: str = "15m"
    WARMUP_MINUTES: int = 45   # 09:15 + 45m = 10:00 AM on NSE/BSE
    CUTOFF_MINUTES: int = 120  # 15:30 - 2h = 1:30 PM on NSE/BSE

    # Strategy Technical & Risk Parameters
    SWING_HIGH_BARS: int = 3            # 3-bar swing high lookback
    MIN_SL_BUFFER_PCT: float = 0.0020   # 0.2% min SL buffer above entry
    SWING_SL_BUFFER_PCT: float = 0.0005 # 0.05% anti-wick buffer above swing high
    RISK_REWARD_RATIO: float = 2.0      # 1:2 R:R target
    ADX_PERIOD: int = 14
    ADX_THRESHOLD: float = 25.0
    RSI_PERIOD: int = 14
    STOCH_PERIOD: int = 14
    STOCH_K_PERIOD: int = 3
    STOCH_D_PERIOD: int = 3
    STOCH_OVERBOUGHT: float = 80.0

    def evaluate_signals(
        self, 
        df: pd.DataFrame, 
        nifty_pct_map: Optional[pd.Series] = None,
        config: TradingConfig = CONFIG
    ) -> Optional[pd.DataFrame]:
        """
        Evaluates the VWAP-Stoch Breakdown strategy criteria on 15m candle DataFrames.
        Returns enriched DataFrame with boolean 'Signal' column, or None if data is insufficient.
        """
        if df.empty or len(df) < 50:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # 1. Compute Required Indicators
        df = compute_stoch_rsi(df)
        df = compute_adx(df)
        df = compute_vwap(df)

        if 'Stoch_K' not in df.columns or 'ADX' not in df.columns or 'VWAP' not in df.columns:
            return None

        # 2. Add Relative Weakness Filter
        df = compute_relative_weakness(df, nifty_pct_map)

        # 3. Time Filter: Dynamic Strategy Entry Window via relative offsets
        market_key = getattr(config, 'EXCHANGE_MARKET', 'NSE')
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

        # 4. Explicit Strategy Sub-Filter Boolean Flags (for telemetry & diagnostics)
        df['Rel_Weakness_Pass'] = df['Rel_Weakness'].fillna(False)
        df['VWAP_Pass'] = (df['Close'] < df['VWAP'])
        df['ADX_Pass'] = (df['ADX'] > self.ADX_THRESHOLD)
        df['Stoch_Pass'] = (df['Stoch_K_prev'] >= self.STOCH_OVERBOUGHT) & (df['Stoch_K'] < self.STOCH_OVERBOUGHT)

        # 5. Generate Strategy Entry Signals
        df['Signal'] = (
            time_filter &
            df['Rel_Weakness_Pass'] &
            df['VWAP_Pass'] &
            df['ADX_Pass'] &
            df['Stoch_Pass']
        )

        return df

    def calculate_stop_and_target(
        self, 
        df: pd.DataFrame, 
        entry_idx: int
    ) -> Tuple[float, float, float]:
        """
        Calculates Stop-Loss, Target, and absolute Risk for a SHORT breakdown trade.
        """
        entry_price = float(df.iloc[entry_idx]['Close'])
        if entry_price <= 0:
            return 0.0, 0.0, 0.0

        swing_start = max(0, entry_idx - self.SWING_HIGH_BARS)
        swing_high = float(df.iloc[swing_start : entry_idx]['High'].max()) if entry_idx > 0 else entry_price

        sl_swing = swing_high * (1.0 + self.SWING_SL_BUFFER_PCT)
        sl_min = entry_price * (1.0 + self.MIN_SL_BUFFER_PCT)
        sl_price = max(sl_swing, sl_min)
        risk = sl_price - entry_price
        target_price = entry_price - (risk * self.RISK_REWARD_RATIO)
        return round(sl_price, 2), round(target_price, 2), round(risk, 2)


# Default Singleton Strategy Instance
STRATEGY_INSTANCE = VWAPStochBreakdownStrategy()
STRATEGY_NAME = STRATEGY_INSTANCE.NAME
STRATEGY_VERSION = STRATEGY_INSTANCE.VERSION
TIMEFRAME = STRATEGY_INSTANCE.TIMEFRAME
SWING_HIGH_BARS = STRATEGY_INSTANCE.SWING_HIGH_BARS
MIN_SL_BUFFER_PCT = STRATEGY_INSTANCE.MIN_SL_BUFFER_PCT
SWING_SL_BUFFER_PCT = STRATEGY_INSTANCE.SWING_SL_BUFFER_PCT
RISK_REWARD_RATIO = STRATEGY_INSTANCE.RISK_REWARD_RATIO


def evaluate_signals(df: pd.DataFrame, nifty_pct_map: Optional[pd.Series] = None, config: TradingConfig = CONFIG) -> Optional[pd.DataFrame]:
    """Functional interface forwarding to STRATEGY_INSTANCE."""
    return STRATEGY_INSTANCE.evaluate_signals(df, nifty_pct_map, config=config)


def calculate_stop_and_target(
    entry_price: float,
    swing_high: float,
    min_sl_buffer_pct: float = MIN_SL_BUFFER_PCT,
    swing_sl_buffer_pct: float = SWING_SL_BUFFER_PCT,
    risk_reward_ratio: float = RISK_REWARD_RATIO
) -> Tuple[float, float, float]:
    """Calculates SL, Target, and absolute Risk for a short trade."""
    if entry_price <= 0:
        return 0.0, 0.0, 0.0
    sl_swing = swing_high * (1.0 + swing_sl_buffer_pct)
    sl_min = entry_price * (1.0 + min_sl_buffer_pct)
    sl_price = max(sl_swing, sl_min)
    risk = sl_price - entry_price
    target_price = entry_price - (risk * risk_reward_ratio)
    return round(sl_price, 2), round(target_price, 2), round(risk, 2)


def simulate_single_trade(
    df: pd.DataFrame, 
    entry_idx: int, 
    ticker: str,
    config: TradingConfig = CONFIG
) -> Optional[Dict[str, Any]]:
    """
    Simulates the forward lifecycle of a single short position until exit.
    """
    if entry_idx >= len(df) - 1:
        return None

    entry_t = df.index[entry_idx]
    entry_p = float(df.iloc[entry_idx]['Close'])
    sl, tp, risk = STRATEGY_INSTANCE.calculate_stop_and_target(df, entry_idx)
    risk_pct = risk / entry_p if entry_p > 0 else 0.0

    exit_t, pnl_pct, result = None, 0.0, ''
    curr_sl = sl
    trailed = False

    for i in range(entry_idx + 1, len(df)):
        t_bar = df.index[i]
        h_val = float(df.iloc[i]['High'])
        l_val = float(df.iloc[i]['Low'])
        c_val = float(df.iloc[i]['Close'])

        # 1. Check if Stop-Loss is hit
        if h_val >= curr_sl:
            exit_t = t_bar
            pnl_pct = 0.0 if trailed else -risk_pct
            result = TradeExitReason.TRAILING_SL_HIT if trailed else TradeExitReason.SL_HIT
            break

        # 2. Check if Target is hit
        elif l_val <= tp:
            exit_t, pnl_pct, result = t_bar, (STRATEGY_INSTANCE.RISK_REWARD_RATIO * risk_pct), TradeExitReason.TARGET_HIT
            break

        # 3. Check Trailing SL
        new_sl = STRATEGY_INSTANCE.calculate_trailing_stop(entry_p, curr_sl, c_val, h_val, l_val, risk)
        if new_sl is not None and not trailed:
            curr_sl = new_sl
            trailed = True

        # 4. Check Mandatory Strategy & Platform Fail-Safe Square-Off
        effective_sq = STRATEGY_INSTANCE.get_effective_squareoff_time(getattr(config, 'EXCHANGE_MARKET', 'NSE'))
        if effective_sq is not None and t_bar.time() >= effective_sq:
            exit_t, pnl_pct, result = t_bar, (entry_p - c_val) / entry_p, TradeExitReason.ALGO_SQUAREOFF_DAY_END
            break

    if exit_t:
        return {
            'Symbol': ticker,
            'Entry Time': entry_t,
            'Entry Price': entry_p,
            'Exit Time': exit_t,
            'PnL %': pnl_pct,
            'Result': result
        }
    return None
