"""
Strategy: VWAP-Stoch Breakdown (15m Intraday Short)

Strategy Rules:
  - Direction: Intraday Short Breakdown (MIS)
  - Timeframe: 15-Minute Candles
  - Entry Window: 10:00 AM – 01:30 PM IST
  - Relative Weakness: Stock Intraday % < NIFTY 50 Intraday %
  - Technical Triggers:
      1. Stoch RSI %K crosses below 80 from overbought (Stoch_K_prev >= 80 and Stoch_K < 80)
      2. ADX > 25 (Trend strength filter)
      3. Close < VWAP (Bearish intraday bias)
  - Risk Management:
      1. Initial Stop Loss: 3-bar swing high (min 0.2% buffer)
      2. Profit Target: Dynamic 1:2 Risk-to-Reward (2R)
      3. Trailing Stop Loss: Move SL to Breakeven when trade reaches +1R profit
      4. Auto-Squareoff: Hard exit at 15:00 IST (3:00 PM)
"""

import pandas as pd
from typing import Optional, Dict, Any
from config import CONFIG, TradingConfig
from core.indicators import compute_stoch_rsi, compute_adx, compute_vwap, compute_relative_weakness
from core.trade_db import TradeExitReason
from core.market_calendar import get_strategy_entry_window, get_squareoff_time, is_continuous_market

STRATEGY_NAME = "VWAP-Stoch Breakdown"
STRATEGY_VERSION = "1.0.0"

# Strategy Execution Timing Rules (Relative Offsets in Minutes)
STRATEGY_WARMUP_MINUTES = 45   # 09:15 + 45m = 10:00 AM on NSE/BSE
STRATEGY_CUTOFF_MINUTES = 120  # 15:30 - 2h = 1:30 PM on NSE/BSE


def evaluate_signals(
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
            warmup_minutes=STRATEGY_WARMUP_MINUTES,
            cutoff_minutes=STRATEGY_CUTOFF_MINUTES
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
    df['ADX_Pass'] = (df['ADX'] > 25)
    df['Stoch_Pass'] = (df['Stoch_K_prev'] >= 80) & (df['Stoch_K'] < 80)

    # 5. Generate Strategy Entry Signals
    df['Signal'] = (
        time_filter &
        df['Rel_Weakness_Pass'] &
        df['VWAP_Pass'] &
        df['ADX_Pass'] &
        df['Stoch_Pass']
    )

    return df


def simulate_single_trade(
    df: pd.DataFrame, 
    entry_idx: int, 
    ticker: str,
    config: TradingConfig = CONFIG
) -> Optional[Dict[str, Any]]:
    """
    Simulates the forward lifecycle of a single VWAP-Stoch Breakdown short position from entry_idx.
    Enforces:
      1. Initial Stop Loss vs +1R Trailed Stop Loss (Breakeven).
      2. Dynamic 1:2 Risk-to-Reward Target.
      3. Intraday Auto-Squareoff (Default: 3:00 PM).
    Returns a trade dictionary or None if no exit was reached.
    """
    entry_t = df.index[entry_idx]
    entry_p = df.iloc[entry_idx]['Close']
    swing_high = df.iloc[entry_idx - config.SWING_HIGH_BARS : entry_idx]['High'].max()
    sl = max(swing_high * (1.0 + config.SWING_SL_BUFFER_PCT), entry_p * (1.0 + config.MIN_SL_BUFFER_PCT))
    risk = sl - entry_p
    risk_pct = risk / entry_p
    tp = entry_p - (config.RISK_REWARD_RATIO * risk)

    exit_t, pnl_pct, result = None, 0.0, ''
    curr_sl = sl
    trailed = False

    # Extract NumPy arrays for fast inner-loop traversal (50x faster than df.iloc)
    highs = df['High'].values
    lows = df['Low'].values
    closes = df['Close'].values
    timestamps = df.index

    for j in range(entry_idx + 1, len(df)):
        h_val = highs[j]
        l_val = lows[j]
        c_val = closes[j]
        t_bar = timestamps[j]

        # 1. Check if current SL is hit
        if h_val >= curr_sl:
            if trailed:
                exit_t, pnl_pct, result = t_bar, 0.0, TradeExitReason.TRAILING_SL_HIT
            else:
                exit_t, pnl_pct, result = t_bar, -risk_pct, TradeExitReason.SL_HIT
            break

        # 2. Check if Target is hit
        elif l_val <= tp:
            exit_t, pnl_pct, result = t_bar, (config.RISK_REWARD_RATIO * risk_pct), TradeExitReason.TARGET_HIT
            break

        # 3. Check if +1R profit threshold is reached to trail SL to Breakeven
        if not trailed and l_val <= (entry_p - risk):
            curr_sl = entry_p
            trailed = True

        # 4. Check Configurable Square-Off (derived dynamically from market calendar)
        sq_time = get_squareoff_time(getattr(config, 'EXCHANGE_MARKET', 'NSE'))
        if sq_time is not None and t_bar.time() >= sq_time:
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
