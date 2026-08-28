"""
strategies/vwap_stoch_trend/v1_1.py
Bi-Directional VWAP + Stochastic RSI Intraday Trend Strategy (v1.1).

Refinements in v1.1:
  1. NIFTY Macro Regime Point Filter:
     - Long only when NIFTY is positive by > +30 points from day open.
     - Short only when NIFTY is negative by > -30 points from day open.
  2. Overextension Filter:
     - Stock must not have moved more than 2.0% intraday before signal bar.
  3. Core Momentum & Trend:
     - Relative Strength / Weakness vs NIFTY benchmark.
     - Price position relative to VWAP.
     - Stochastic RSI oversold/overbought hook confirmation.
     - ADX(14) > 25.0 trend strength.
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
STRATEGY_VERSION = "v1_1"
TIMEFRAME = "15m"
SWING_BARS = 3


class VWAPStochTrendStrategyV11(BaseStrategy):
    NAME: str = "VWAP-Stoch Trend"
    VERSION: str = "1.1.0"
    TIMEFRAME: str = "15m"
    WARMUP_MINUTES: int = 45
    CUTOFF_MINUTES: int = 120
    SQUAREOFF_MINUTES_BEFORE_CLOSE: int = 30

    SWING_BARS: int = 3
    MIN_SL_BUFFER_PCT: float = 0.0020
    SWING_SL_BUFFER_PCT: float = 0.0005
    RISK_REWARD_RATIO: float = 2.0
    ADX_THRESHOLD: float = 25.0
    STOCH_OVERBOUGHT: float = 80.0
    STOCH_OVERSOLD: float = 20.0
    NIFTY_POINTS_THRESHOLD: float = 30.0
    MAX_STOCK_INTRADAY_MOVE_PCT: float = 0.02

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

        # 1. Compute Indicators
        df = compute_stoch_rsi(df)
        df = compute_adx(df)
        df = compute_vwap(df)

        if 'Stoch_K' not in df.columns or 'ADX' not in df.columns or 'VWAP' not in df.columns:
            return None

        # 2. Compute Relative Strength and Weakness
        df = compute_relative_weakness(df, nifty_pct_map)
        df['Rel_Weakness_Pass'] = df['Rel_Weakness'].fillna(False)
        df['Rel_Strength_Pass'] = ~df['Rel_Weakness_Pass']

        # 3. Stock Intraday Move & Overextension Check (< 2%)
        daily_open = df.groupby(df.index.date)['Open'].transform('first')
        df['Stock_Intraday_Pct'] = (df['Close'] - daily_open) / daily_open
        max_move = getattr(config, 'MAX_STOCK_INTRADAY_MOVE_PCT', self.MAX_STOCK_INTRADAY_MOVE_PCT)
        df['Not_Overextended'] = df['Stock_Intraday_Pct'].abs() <= max_move

        # 4. Time Filter
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

        # 5. NIFTY Points Filter (+30 for Long / -30 for Short)
        nifty_pts_thresh = getattr(config, 'NIFTY_POINTS_THRESHOLD', self.NIFTY_POINTS_THRESHOLD)
        if nifty_pct_map is not None:
            df['Nifty_Pct'] = df.index.map(lambda t: nifty_pct_map.get(t, 0.0) if hasattr(nifty_pct_map, 'get') else 0.0)
            df['Nifty_Pts_Est'] = df['Nifty_Pct'] * 24000.0
            nifty_long_pass = df['Nifty_Pts_Est'] >= nifty_pts_thresh
            nifty_short_pass = df['Nifty_Pts_Est'] <= -nifty_pts_thresh
        else:
            nifty_long_pass = pd.Series(True, index=df.index)
            nifty_short_pass = pd.Series(True, index=df.index)

        # 6. Technical Indicator Triggers
        adx_thresh = getattr(config, 'ADX_THRESHOLD', self.ADX_THRESHOLD) if config else self.ADX_THRESHOLD
        df['ADX_Pass'] = df['ADX'] > adx_thresh
        df['VWAP_Long_Pass'] = df['Close'] > df['VWAP']
        df['VWAP_Short_Pass'] = df['Close'] < df['VWAP']
        df['Stoch_Long_Pass'] = (df['Stoch_K_prev'] <= self.STOCH_OVERSOLD) & (df['Stoch_K'] > self.STOCH_OVERSOLD)
        df['Stoch_Short_Pass'] = (df['Stoch_K_prev'] >= self.STOCH_OVERBOUGHT) & (df['Stoch_K'] < self.STOCH_OVERBOUGHT)

        # 7. Directional Signals
        df['Long_Signal'] = (
            time_filter &
            nifty_long_pass &
            df['Not_Overextended'] &
            df['Rel_Strength_Pass'] &
            df['VWAP_Long_Pass'] &
            df['ADX_Pass'] &
            df['Stoch_Long_Pass']
        )
        df['Short_Signal'] = (
            time_filter &
            nifty_short_pass &
            df['Not_Overextended'] &
            df['Rel_Weakness_Pass'] &
            df['VWAP_Short_Pass'] &
            df['ADX_Pass'] &
            df['Stoch_Short_Pass']
        )

        df['Signal'] = df['Long_Signal'] | df['Short_Signal']
        df['Direction'] = np.where(df['Long_Signal'], 'LONG', np.where(df['Short_Signal'], 'SHORT', 'NONE'))
        return df

    def calculate_stop_and_target(
        self,
        df: pd.DataFrame,
        entry_idx: int,
        config: Optional[TradingConfig] = None,
        direction: str = "SHORT",
        **kwargs
    ) -> Tuple[float, float, float]:
        entry_price = float(df.iloc[entry_idx]['Close'])
        min_buf = getattr(config, 'MIN_SL_BUFFER_PCT', self.MIN_SL_BUFFER_PCT)
        swing_buf = getattr(config, 'SWING_SL_BUFFER_PCT', self.SWING_SL_BUFFER_PCT)
        rr_ratio = getattr(config, 'RISK_REWARD_RATIO', self.RISK_REWARD_RATIO)
        swing_b = getattr(config, 'SWING_HIGH_BARS', getattr(config, 'SWING_BARS', self.SWING_BARS))

        start_idx = max(0, entry_idx - swing_b)
        
        if direction == "LONG":
            swing_low = float(df.iloc[start_idx:entry_idx]['Low'].min()) if entry_idx > start_idx else entry_price * (1.0 - min_buf)
            sl_candidate = swing_low * (1.0 - swing_buf)
            min_sl = entry_price * (1.0 - min_buf)
            sl_price = min(sl_candidate, min_sl)
            risk = entry_price - sl_price
            target_price = entry_price + (risk * rr_ratio)
        else: # SHORT
            swing_high = float(df.iloc[start_idx:entry_idx]['High'].max()) if entry_idx > start_idx else entry_price * (1.0 + min_buf)
            sl_candidate = swing_high * (1.0 + swing_buf)
            min_sl = entry_price * (1.0 + min_buf)
            sl_price = max(sl_candidate, min_sl)
            risk = sl_price - entry_price
            target_price = entry_price - (risk * rr_ratio)

        return round(sl_price, 2), round(target_price, 2), round(risk, 2)

    def calculate_trailing_stop(
        self,
        entry_price: float,
        current_sl: float,
        current_close: float,
        current_high: float,
        current_low: float,
        initial_risk: float,
        direction: str = "SHORT"
    ) -> Optional[float]:
        if direction == "LONG":
            if current_high >= entry_price + initial_risk and current_sl < entry_price:
                return entry_price
        else: # SHORT
            if current_low <= entry_price - initial_risk and current_sl > entry_price:
                return entry_price
        return None


STRATEGY_INSTANCE = VWAPStochTrendStrategyV11()


def evaluate_signals(
    df: pd.DataFrame,
    nifty_pct_map: Optional[pd.Series] = None,
    config: Optional[TradingConfig] = None
) -> Optional[pd.DataFrame]:
    return STRATEGY_INSTANCE.evaluate_signals(df, nifty_pct_map, config=config)


def calculate_stop_and_target(entry_price: float, swing_ref: float, config: Optional[TradingConfig] = None, direction: str = "SHORT", **kwargs) -> Tuple[float, float, float]:
    min_buf = getattr(config, 'MIN_SL_BUFFER_PCT', 0.0020)
    swing_buf = getattr(config, 'SWING_SL_BUFFER_PCT', 0.0005)
    rr_ratio = getattr(config, 'RISK_REWARD_RATIO', 2.0)

    if direction == "LONG":
        sl_candidate = swing_ref * (1.0 - swing_buf)
        min_sl = entry_price * (1.0 - min_buf)
        sl_price = min(sl_candidate, min_sl)
        risk = entry_price - sl_price
        target_price = entry_price + (risk * rr_ratio)
    else: # SHORT
        sl_candidate = swing_ref * (1.0 + swing_buf)
        min_sl = entry_price * (1.0 + min_buf)
        sl_price = max(sl_candidate, min_sl)
        risk = sl_price - entry_price
        target_price = entry_price - (risk * rr_ratio)

    return round(sl_price, 2), round(target_price, 2), round(risk, 2)


def calculate_trailing_stop(entry_price: float, current_sl: float, current_close: float, current_high: float, current_low: float, initial_risk: float, direction: str = "SHORT", **kwargs) -> Optional[float]:
    return STRATEGY_INSTANCE.calculate_trailing_stop(entry_price, current_sl, current_close, current_high, current_low, initial_risk, direction=direction)


def simulate_single_trade(
    df: pd.DataFrame, 
    entry_idx: int, 
    ticker: str,
    config: TradingConfig = CONFIG
) -> Optional[Dict[str, Any]]:
    if entry_idx >= len(df) - 1:
        return None

    entry_t = df.index[entry_idx]
    entry_p = float(df.iloc[entry_idx]['Close'])
    direction = str(df.iloc[entry_idx].get('Direction', 'SHORT'))
    if direction not in ['LONG', 'SHORT']:
        return None

    sl, tp, risk = STRATEGY_INSTANCE.calculate_stop_and_target(df, entry_idx, config=config, direction=direction)
    risk_pct = risk / entry_p if entry_p > 0 else 0.0

    exit_t, pnl_pct, result = None, 0.0, ''
    curr_sl = sl
    trailed = False

    for i in range(entry_idx + 1, len(df)):
        t_bar = df.index[i]
        h_val = float(df.iloc[i]['High'])
        l_val = float(df.iloc[i]['Low'])
        c_val = float(df.iloc[i]['Close'])

        if direction == "LONG":
            if l_val <= curr_sl:
                exit_t = t_bar
                pnl_pct = 0.0 if trailed else -risk_pct
                result = TradeExitReason.TRAILING_SL_HIT if trailed else TradeExitReason.SL_HIT
                break
            elif h_val >= tp:
                exit_t, pnl_pct, result = t_bar, (STRATEGY_INSTANCE.RISK_REWARD_RATIO * risk_pct), TradeExitReason.TARGET_HIT
                break
            new_sl = STRATEGY_INSTANCE.calculate_trailing_stop(entry_p, curr_sl, c_val, h_val, l_val, risk, direction="LONG")
            if new_sl is not None and not trailed:
                curr_sl = new_sl
                trailed = True
            effective_sq = STRATEGY_INSTANCE.get_effective_squareoff_time(getattr(config, 'EXCHANGE_MARKET', 'NSE'))
            if effective_sq is not None and t_bar.time() >= effective_sq:
                exit_t, pnl_pct, result = t_bar, (c_val - entry_p) / entry_p, TradeExitReason.ALGO_SQUAREOFF_DAY_END
                break

        else: # SHORT
            if h_val >= curr_sl:
                exit_t = t_bar
                pnl_pct = 0.0 if trailed else -risk_pct
                result = TradeExitReason.TRAILING_SL_HIT if trailed else TradeExitReason.SL_HIT
                break
            elif l_val <= tp:
                exit_t, pnl_pct, result = t_bar, (STRATEGY_INSTANCE.RISK_REWARD_RATIO * risk_pct), TradeExitReason.TARGET_HIT
                break
            new_sl = STRATEGY_INSTANCE.calculate_trailing_stop(entry_p, curr_sl, c_val, h_val, l_val, risk, direction="SHORT")
            if new_sl is not None and not trailed:
                curr_sl = new_sl
                trailed = True
            effective_sq = STRATEGY_INSTANCE.get_effective_squareoff_time(getattr(config, 'EXCHANGE_MARKET', 'NSE'))
            if effective_sq is not None and t_bar.time() >= effective_sq:
                exit_t, pnl_pct, result = t_bar, (entry_p - c_val) / entry_p, TradeExitReason.ALGO_SQUAREOFF_DAY_END
                break

    if exit_t:
        exit_p = float(df.loc[exit_t, 'Close']) if exit_t in df.index else entry_p * (1.0 + pnl_pct if direction == "LONG" else 1.0 - pnl_pct)
        if result == TradeExitReason.SL_HIT:
            exit_p = sl
        elif result == TradeExitReason.TRAILING_SL_HIT:
            exit_p = entry_p
        elif result == TradeExitReason.TARGET_HIT:
            exit_p = tp
        return {
            'Symbol': ticker,
            'Direction': direction,
            'Entry Time': entry_t,
            'Entry Price': round(entry_p, 2),
            'Stop Loss Price': round(sl, 2),
            'Target Price': round(tp, 2),
            'Exit Time': exit_t,
            'Exit Price': round(exit_p, 2),
            'PnL %': pnl_pct,
            'Result': result
        }
    return None
