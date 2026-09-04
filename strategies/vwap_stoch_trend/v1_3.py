"""
================================================================================
STRATEGY SPECIFICATION & ARCHITECTURE
================================================================================
Strategy Name       : VWAP-Stoch Trend
Version             : 1.2.0 (Bidirectional Runner - Default Production)
Author / Owner      : Algorithmic Trading Team
Status              : Production / Active Default

1. CORE SETUP & UNIVERSE
--------------------------------------------------------------------------------
Primary Timeframe   : 15m (15-Minute Candles)
Trading Universe    : NIFTY 200 (Constituents with DuckDB / Live Stream)
Direction           : Bidirectional (Long & Short)
Benchmark Reference : NIFTY 50 / NIFTY 200 Composite (% Change from Open)

2. INDICATORS & PARAMETERS
--------------------------------------------------------------------------------
Indicators Used     : 1. VWAP (Intraday Volume Weighted Average Price)
                      2. Stochastic RSI (K=14, D=3, Stoch=14, RSI=14)
                      3. ADX (Period=14, Trend Threshold >= 25.0)
                      4. Relative Strength & Weakness vs. Benchmark

3. TIMING & SESSION CONSTRAINTS
--------------------------------------------------------------------------------
Market Open Warmup  : 10:00 AM IST (Warmup minutes = 45 after 09:15)
Entry Cutoff Time   : 01:30 PM IST (Cutoff minutes = 120 before 15:30)
Mandatory Square-off: 03:00 PM IST (All open runners auto-exited at 3:00 PM)

4. ENTRY & EXIT RULES
--------------------------------------------------------------------------------
Long Entry Rules    : - Stock % from Day Open > Benchmark % from Day Open (Rel Strength)
                      - Close > VWAP
                      - Stoch RSI K crosses above 20 from oversold (Stoch_K_prev <= 20 and Stoch_K > 20)
                      - ADX(14) >= 25.0 (Strong trend presence)
Short Entry Rules   : - Stock % from Day Open < Benchmark % from Day Open (Rel Weakness)
                      - Close < VWAP
                      - Stoch RSI K crosses below 80 from overbought (Stoch_K_prev >= 80 and Stoch_K < 80)
                      - ADX(14) >= 25.0 (Strong trend presence)
Exit Target (TP)    : 1:1.5 Risk-to-Reward Ratio (Initial target order)
Stop Loss (SL)      : 3-Bar Swing High (Shorts) / Swing Low (Longs) + Buffer
Trailing Stop Loss  : Move SL to Breakeven (+0.1% buffer) once trade gains +1.0R profit
Runner Logic        : Profitable trades ride the intraday trend until 3:00 PM Square-off

5. RISK MANAGEMENT & SIZING
--------------------------------------------------------------------------------
Position Sizing     : Equal-Split Slot Margin with 5x MIS Leverage (2 Concurrent Slots)
Max Concurrent Slots: 2 Active Positions
Daily Max Loss Limit: 3.0% of Day Starting Capital (Strategy-Level Constraint)
Priority Hierarchy  : Overrides platform default (4.0% in config.py / core.risk) with tighter 3.0% limit
Emergency Actions   : Halts all trading & rejects all new signals for rest of day if limit hit

6. STATISTICAL EXPECTANCY (10-MONTH DUCKDB BASELINE)
--------------------------------------------------------------------------------
Win Rate            : ~42.3%
Gross Profit Factor : 1.28
Net Realized ROI    : +56.56% (after all statutory taxes & charges)
================================================================================
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
STRATEGY_VERSION = "v1_3"
TIMEFRAME = "15m"
SWING_BARS = 3


class VWAPStochTrendStrategyV13(BaseStrategy):
    NAME: str = "VWAP-Stoch Trend"
    VERSION: str = "1.3.0"
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

    def calculate_confidence_score(self, df: pd.DataFrame) -> pd.Series:
        """
        Encapsulated 4-Pillar Strategy Confidence Scoring Formula (0.0 to 100.0).
        """
        if df is None or df.empty or 'Close' not in df.columns:
            return pd.Series(0.0, index=df.index if df is not None else [0])

        # 1. ADX Trend Score (40% weight): Normalized 25 -> 50+ to 0 -> 100
        adx_series = df.get('ADX', pd.Series(0.0, index=df.index)).fillna(0.0)
        adx_norm = np.clip((adx_series - 25.0) / 25.0 * 100.0, 0.0, 100.0)

        # 2. Relative Momentum Spread Score vs NIFTY (35% weight): Normalized 0% -> 2.5%+ to 0 -> 100
        stock_pct = df.get('Stock_Pct', pd.Series(0.0, index=df.index)).fillna(0.0)
        nifty_pct = df.get('Nifty_Pct', pd.Series(0.0, index=df.index)).fillna(0.0)
        direction = df.get('Direction', pd.Series('NONE', index=df.index))

        rel_spread = np.where(
            direction == 'LONG',
            stock_pct - nifty_pct,
            nifty_pct - stock_pct
        )
        rel_norm = np.clip(rel_spread / 0.025 * 100.0, 0.0, 100.0)

        # 3. VWAP Displacement Distance Score (15% weight): Normalized 0% -> 2% distance
        vwap_series = df.get('VWAP', df['Close']).fillna(df['Close'])
        vwap_dist = np.abs(df['Close'] - vwap_series) / np.maximum(vwap_series, 1e-6)
        vwap_norm = np.clip(vwap_dist / 0.020 * 100.0, 0.0, 100.0)

        # 4. Stochastic RSI Hook Magnitude Score (10% weight): Magnitude of threshold penetration
        stoch_k = df.get('Stoch_K', pd.Series(50.0, index=df.index)).fillna(50.0)
        stoch_mag = np.where(
            direction == 'LONG',
            stoch_k - 20.0,
            80.0 - stoch_k
        )
        stoch_norm = np.clip(stoch_mag / 20.0 * 100.0, 0.0, 100.0)

        # Composite Strategy Confidence Score
        confidence = (
            0.40 * adx_norm +
            0.35 * rel_norm +
            0.15 * vwap_norm +
            0.10 * stoch_norm
        ).round(2)

        return confidence

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

        # 3. Macro Market Alignment for Longs (Nifty Positive / Trending)
        if 'Nifty_Pct' in df.columns:
            df['Nifty_Macro_Bull'] = df['Nifty_Pct'] >= 0.0
        else:
            df['Nifty_Macro_Bull'] = True

        # 4. Time Filter (10:00 AM to 1:30 PM)
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

        # 5. Trend Strength confirmation
        adx_thresh = getattr(config, 'ADX_THRESHOLD', self.ADX_THRESHOLD)
        df['ADX_Pass'] = df['ADX'] >= adx_thresh

        # 6. Stochastic RSI Hooks
        stoch_ob = getattr(config, 'STOCH_OVERBOUGHT', self.STOCH_OVERBOUGHT)
        stoch_os = getattr(config, 'STOCH_OVERSOLD', self.STOCH_OVERSOLD)
        df['Stoch_Short_Pass'] = (df['Stoch_K_prev'] >= stoch_ob) & (df['Stoch_K'] < stoch_ob)
        df['Stoch_Long_Pass'] = (df['Stoch_K_prev'] <= stoch_os) & (df['Stoch_K'] > stoch_os)

        # 7. VWAP Positioning
        df['VWAP_Short_Pass'] = df['Close'] < df['VWAP']
        df['VWAP_Long_Pass'] = df['Close'] > df['VWAP']

        # 8. Directional Signal Triggers
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
            df['Stoch_Long_Pass'] &
            df['Nifty_Macro_Bull']
        )

        df['Signal'] = df['Short_Signal'] | df['Long_Signal']
        df['Direction'] = np.where(df['Short_Signal'], 'SHORT', np.where(df['Long_Signal'], 'LONG', 'NONE'))
        df['Confidence_Score'] = self.calculate_confidence_score(df)

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


STRATEGY_INSTANCE = VWAPStochTrendStrategyV13()
VWAPStochTrendStrategy = VWAPStochTrendStrategyV13


def calculate_stop_and_target(
    entry_price: float,
    swing_ref: float,
    config: Optional[TradingConfig] = None,
    direction: str = "SHORT",
    **kwargs
) -> Tuple[float, float, float]:
    rr_ratio = getattr(config or CONFIG, 'RISK_REWARD_RATIO', STRATEGY_INSTANCE.RISK_REWARD_RATIO)
    min_sl_buffer = STRATEGY_INSTANCE.MIN_SL_BUFFER_PCT
    swing_sl_buffer = STRATEGY_INSTANCE.SWING_SL_BUFFER_PCT

    if str(direction).upper() == "LONG":
        sl_swing = swing_ref * (1.0 - swing_sl_buffer)
        sl_min = entry_price * (1.0 - min_sl_buffer)
        sl_price = min(sl_swing, sl_min)
        risk = entry_price - sl_price
        target_price = entry_price + (risk * rr_ratio)
    else:
        sl_swing = swing_ref * (1.0 + swing_sl_buffer)
        sl_min = entry_price * (1.0 + min_sl_buffer)
        sl_price = max(sl_swing, sl_min)
        risk = sl_price - entry_price
        target_price = entry_price - (risk * rr_ratio)

    return round(sl_price, 2), round(target_price, 2), round(risk, 2)


def calculate_trailing_stop(
    entry_price: float,
    current_sl: float,
    current_close: float,
    current_high: float,
    current_low: float,
    risk: float,
    direction: str = "SHORT"
) -> Optional[float]:
    if str(direction).upper() == "LONG":
        if current_high >= (entry_price + risk):
            return max(current_sl, round(entry_price * 1.001, 2))
    else:
        if current_low <= (entry_price - risk):
            return min(current_sl, round(entry_price * 0.999, 2))
    return None


def evaluate_signals(df: pd.DataFrame, nifty_pct_map: Optional[pd.Series] = None, config: Optional[TradingConfig] = None) -> Optional[pd.DataFrame]:
    return STRATEGY_INSTANCE.evaluate_signals(df, nifty_pct_map, config)


def simulate_single_trade(
    df: pd.DataFrame, 
    entry_idx: int, 
    ticker: str,
    config: TradingConfig = CONFIG
) -> Optional[Dict[str, Any]]:
    if entry_idx >= len(df) - 1:
        return None

    direction = str(df.iloc[entry_idx].get('Direction', 'SHORT')).upper()
    if direction not in ('LONG', 'SHORT'):
        direction = 'SHORT' if bool(df.iloc[entry_idx].get('Short_Signal', False)) else 'LONG'

    entry_t = df.index[entry_idx]
    entry_p = float(df.iloc[entry_idx]['Close'])
    sl, tp, risk = STRATEGY_INSTANCE.calculate_stop_and_target(df, entry_idx, direction=direction)
    risk_pct = risk / entry_p if entry_p > 0 else 0.0

    if risk <= 0 or (risk / entry_p) > 0.025:
        return None

    exit_t, pnl_pct, result, exit_p = None, 0.0, '', entry_p
    curr_sl = sl
    trailed = False

    for i in range(entry_idx + 1, len(df)):
        t_bar = df.index[i]
        h_val = float(df.iloc[i]['High'])
        l_val = float(df.iloc[i]['Low'])
        c_val = float(df.iloc[i]['Close'])

        if direction == 'LONG':
            if not trailed and h_val >= (entry_p + risk):
                curr_sl = max(curr_sl, round(entry_p * 1.001, 2))
                trailed = True

            if l_val <= curr_sl:
                exit_t = t_bar
                exit_p = curr_sl
                pnl_pct = (curr_sl - entry_p) / entry_p
                result = TradeExitReason.TRAILING_SL_HIT if trailed else TradeExitReason.SL_HIT
                break
            elif h_val >= tp:
                exit_t = t_bar
                exit_p = tp
                pnl_pct = STRATEGY_INSTANCE.RISK_REWARD_RATIO * risk_pct
                result = TradeExitReason.TARGET_HIT
                break
        else: # SHORT
            if not trailed and l_val <= (entry_p - risk):
                curr_sl = min(curr_sl, round(entry_p * 0.999, 2))
                trailed = True

            if h_val >= curr_sl:
                exit_t = t_bar
                exit_p = curr_sl
                pnl_pct = (entry_p - curr_sl) / entry_p
                result = TradeExitReason.TRAILING_SL_HIT if trailed else TradeExitReason.SL_HIT
                break
            elif l_val <= tp:
                exit_t = t_bar
                exit_p = tp
                pnl_pct = STRATEGY_INSTANCE.RISK_REWARD_RATIO * risk_pct
                result = TradeExitReason.TARGET_HIT
                break

        if (t_bar.hour == 15 and t_bar.minute >= 0) or (t_bar.hour > 15):
            exit_t = t_bar
            exit_p = c_val
            pnl_pct = ((c_val - entry_p)/entry_p) if direction == 'LONG' else ((entry_p - c_val)/entry_p)
            result = TradeExitReason.ALGO_SQUAREOFF_DAY_END
            break

    if not exit_t:
        return None

    conf_score = float(df.iloc[entry_idx].get('Confidence_Score', 50.0))
    return {
        'Symbol': ticker,
        'Direction': direction,
        'Entry Time': entry_t,
        'Exit Time': exit_t,
        'Entry Price': entry_p,
        'Exit Price': exit_p,
        'Stop Loss Price': sl,
        'Target Price': tp,
        'PnL %': pnl_pct,
        'Result': result,
        'Confidence Score': conf_score,
    }
