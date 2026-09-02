"""
================================================================================
STRATEGY SPECIFICATION & ARCHITECTURE
================================================================================
Strategy Name       : VWAP-Stoch Trend
Version             : 1.3.0 (Confidence Ranked Bidirectional - Production Standard)
Author / Owner      : Algorithmic Trading Team
Status              : Production / Active Default

1. CORE SETUP & UNIVERSE
--------------------------------------------------------------------------------
Primary Timeframe   : 15m (15-Minute Candles)
Trading Universe    : NIFTY 200 (Constituents with DuckDB / Live Stream)
Direction           : Bidirectional (Long & Short)
Benchmark Reference : NIFTY 50 / NIFTY 200 Composite (% Change from Open)

2. CONFIDENCE SCORING ENGINE (0.0 to 100.0)
--------------------------------------------------------------------------------
Encapsulates 4-pillar conviction weighting:
  - 40% ADX Trend Conviction (normalized 25 -> 50+ to 0 -> 100)
  - 35% Relative Momentum Spread vs NIFTY (normalized 0% -> 2.5%+ to 0 -> 100)
  - 15% VWAP Displacement Distance (normalized 0% -> 2% to 0 -> 100)
  - 10% Stochastic RSI Hook Severity (normalized threshold penetration)

3. DETERMINISTIC SLOT ALLOCATION
--------------------------------------------------------------------------------
When concurrent signals exceed available portfolio slots, candidate setups are
ranked descending by Confidence Score, ensuring capital is deployed strictly
into the highest-conviction trades first with 100% Backtest <-> Live Parity.
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
    """
    VWAP-Stoch Trend Strategy v1.3 (Bidirectional + Deterministic Confidence Ranking)
    """
    NAME: str = "VWAP-Stoch Trend"
    VERSION: str = "1.3.0"
    TIMEFRAME: str = "15m"
    DIRECTION_MODE: str = "BIDIRECTIONAL"

    SWING_BARS: int = 5
    ADX_THRESHOLD: float = 25.0
    STOCH_OVERBOUGHT: float = 80.0
    STOCH_OVERSOLD: float = 20.0
    WARMUP_MINUTES: int = 45   # 10:00 AM IST
    CUTOFF_MINUTES: int = 120  # 1:30 PM IST

    def calculate_confidence_score(self, df: pd.DataFrame) -> pd.Series:
        """
        Encapsulated 4-Pillar Strategy Confidence Scoring Formula (0.0 to 100.0).
        Evaluates how strongly the current setup confirms the strategy rules.
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

        # 9. Compute Strategy-Specific Confidence Score (0 - 100)
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
        rr_ratio = getattr(CONFIG, 'RISK_REWARD_RATIO', 2.0)
        buffer_pct = getattr(CONFIG, 'SL_BUFFER_PCT', 0.0005)

        if direction == "LONG":
            swing_low = float(df.iloc[swing_start:entry_idx]['Low'].min()) if entry_idx > swing_start else entry_price * 0.99
            raw_sl = swing_low * (1.0 - buffer_pct)
            sl_price = min(raw_sl, entry_price * (1.0 - getattr(CONFIG, 'MIN_SL_PCT', 0.005)))
            sl_price = max(sl_price, entry_price * (1.0 - getattr(CONFIG, 'MAX_SL_PCT', 0.02)))
            risk = entry_price - sl_price
            target_price = entry_price + (risk * rr_ratio)
        else:
            swing_high = float(df.iloc[swing_start:entry_idx]['High'].max()) if entry_idx > swing_start else entry_price * 1.01
            raw_sl = swing_high * (1.0 + buffer_pct)
            sl_price = max(raw_sl, entry_price * (1.0 + getattr(CONFIG, 'MIN_SL_PCT', 0.005)))
            sl_price = min(sl_price, entry_price * (1.0 + getattr(CONFIG, 'MAX_SL_PCT', 0.02)))
            risk = sl_price - entry_price
            target_price = entry_price - (risk * rr_ratio)

        return round(sl_price, 2), round(target_price, 2), round(risk, 2)

    def simulate_single_trade(
        self,
        df: pd.DataFrame,
        entry_idx: int,
        symbol: str,
        config: Optional[TradingConfig] = None
    ) -> Optional[Dict[str, Any]]:
        row = df.iloc[entry_idx]
        direction = str(row.get('Direction', 'SHORT'))
        if direction not in ("LONG", "SHORT"):
            return None

        entry_time = df.index[entry_idx]
        entry_price = float(row['Close'])
        sl_price, target_price, risk = self.calculate_stop_and_target(df, entry_idx, direction=direction)
        confidence_score = float(row.get('Confidence_Score', 50.0))

        if entry_price <= 0 or risk <= 0:
            return None

        trailing_sl = sl_price
        be_activated = False

        market_key = getattr(config, 'EXCHANGE_MARKET', 'NSE') if config else 'NSE'
        squareoff_cutoff = datetime.time(15, 0)

        for j in range(entry_idx + 1, len(df)):
            curr_row = df.iloc[j]
            curr_time = df.index[j]
            high = float(curr_row['High'])
            low = float(curr_row['Low'])
            close = float(curr_row['Close'])

            if curr_time.date() != entry_time.date():
                break

            if direction == "LONG":
                # Trailing Breakeven Trigger (+1R)
                if not be_activated and high >= (entry_price + risk):
                    be_activated = True
                    trailing_sl = entry_price

                # Check SL hit
                if low <= trailing_sl:
                    exit_p = trailing_sl
                    pnl_pct = (exit_p - entry_price) / entry_price
                    res = 'BREAKEVEN_EXIT' if be_activated and exit_p >= entry_price else 'SL_HIT'
                    return {
                        'Symbol': symbol,
                        'Direction': direction,
                        'Entry Time': entry_time,
                        'Entry Price': entry_price,
                        'Stop Loss Price': sl_price,
                        'Target Price': target_price,
                        'Exit Time': curr_time,
                        'Exit Price': exit_p,
                        'PnL %': round(pnl_pct, 4),
                        'Result': res,
                        'Confidence Score': confidence_score,
                    }

                # Check Target hit
                if high >= target_price:
                    exit_p = target_price
                    pnl_pct = (exit_p - entry_price) / entry_price
                    return {
                        'Symbol': symbol,
                        'Direction': direction,
                        'Entry Time': entry_time,
                        'Entry Price': entry_price,
                        'Stop Loss Price': sl_price,
                        'Target Price': target_price,
                        'Exit Time': curr_time,
                        'Exit Price': exit_p,
                        'PnL %': round(pnl_pct, 4),
                        'Result': 'TARGET_HIT',
                        'Confidence Score': confidence_score,
                    }
            else:
                # SHORT Direction
                # Trailing Breakeven Trigger (+1R)
                if not be_activated and low <= (entry_price - risk):
                    be_activated = True
                    trailing_sl = entry_price

                # Check SL hit
                if high >= trailing_sl:
                    exit_p = trailing_sl
                    pnl_pct = (entry_price - exit_p) / entry_price
                    res = 'BREAKEVEN_EXIT' if be_activated and exit_p <= entry_price else 'SL_HIT'
                    return {
                        'Symbol': symbol,
                        'Direction': direction,
                        'Entry Time': entry_time,
                        'Entry Price': entry_price,
                        'Stop Loss Price': sl_price,
                        'Target Price': target_price,
                        'Exit Time': curr_time,
                        'Exit Price': exit_p,
                        'PnL %': round(pnl_pct, 4),
                        'Result': res,
                        'Confidence Score': confidence_score,
                    }

                # Check Target hit
                if low <= target_price:
                    exit_p = target_price
                    pnl_pct = (entry_price - exit_p) / entry_price
                    return {
                        'Symbol': symbol,
                        'Direction': direction,
                        'Entry Time': entry_time,
                        'Entry Price': entry_price,
                        'Stop Loss Price': sl_price,
                        'Target Price': target_price,
                        'Exit Time': curr_time,
                        'Exit Price': exit_p,
                        'PnL %': round(pnl_pct, 4),
                        'Result': 'TARGET_HIT',
                        'Confidence Score': confidence_score,
                    }

            # 3:00 PM Auto Squareoff
            if not is_continuous_market(market_key) and curr_time.time() >= squareoff_cutoff:
                exit_p = close
                pnl_pct = (exit_p - entry_price) / entry_price if direction == "LONG" else (entry_price - exit_p) / entry_price
                return {
                    'Symbol': symbol,
                    'Direction': direction,
                    'Entry Time': entry_time,
                    'Entry Price': entry_price,
                    'Stop Loss Price': sl_price,
                    'Target Price': target_price,
                    'Exit Time': curr_time,
                    'Exit Price': exit_p,
                    'PnL %': round(pnl_pct, 4),
                    'Result': 'ALGO_SQUAREOFF_DAY_END',
                    'Confidence Score': confidence_score,
                }

        # End of series squareoff
        last_time = df.index[-1]
        exit_p = float(df.iloc[-1]['Close'])
        pnl_pct = (exit_p - entry_price) / entry_price if direction == "LONG" else (entry_price - exit_p) / entry_price
        return {
            'Symbol': symbol,
            'Direction': direction,
            'Entry Time': entry_time,
            'Entry Price': entry_price,
            'Stop Loss Price': sl_price,
            'Target Price': target_price,
            'Exit Time': last_time,
            'Exit Price': exit_p,
            'PnL %': round(pnl_pct, 4),
            'Result': 'ALGO_SQUAREOFF_DAY_END',
            'Confidence Score': confidence_score,
        }


# Standard exports
STRATEGY_CLASS = VWAPStochTrendStrategyV13
STRATEGY_INSTANCE = VWAPStochTrendStrategyV13()

evaluate_signals = STRATEGY_INSTANCE.evaluate_signals
calculate_stop_and_target = STRATEGY_INSTANCE.calculate_stop_and_target
simulate_single_trade = STRATEGY_INSTANCE.simulate_single_trade
