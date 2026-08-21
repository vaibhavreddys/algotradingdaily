"""
Technical Indicators Module.

Pure mathematical indicator calculators operating on OHLCV candle DataFrames:
  - Average Directional Index (ADX) & Directional Movement (+DI / -DI)
  - Relative Weakness against Benchmark index
  - Fast Stochastic RSI (%K / %D)
  - Intraday Volume Weighted Average Price (VWAP)
"""

import pandas as pd
import numpy as np
import pandas_ta as ta
from typing import Optional


def compute_adx(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    """Computes ADX (Average Directional Index) trend strength and adds 'ADX' column to df."""
    adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=length)
    if adx_df is not None and f'ADX_{length}' in adx_df.columns:
        df['ADX'] = adx_df[f'ADX_{length}']
    return df


def compute_relative_weakness(df: pd.DataFrame, benchmark_pct_map: Optional[pd.Series] = None) -> pd.DataFrame:
    """
    Computes Stock intraday % return from Day Open and compares against Benchmark index.
    Adds 'Stock_Pct', 'Nifty_Pct', and 'Rel_Weakness' boolean flag to df.
    """
    df['Date'] = df.index.date
    stock_daily_open = df.groupby('Date')['Open'].transform('first')
    df['Stock_Pct'] = (df['Close'] - stock_daily_open) / stock_daily_open

    if benchmark_pct_map is not None and not benchmark_pct_map.empty:
        df['Nifty_Pct'] = benchmark_pct_map.reindex(df.index).ffill()
        df['Rel_Weakness'] = df['Stock_Pct'] < df['Nifty_Pct']
    else:
        df['Rel_Weakness'] = True
    return df


def compute_stoch_rsi(
    df: pd.DataFrame, 
    length: int = 14, 
    rsi_length: int = 14, 
    k: int = 3, 
    d: int = 3
) -> pd.DataFrame:
    """Computes Stochastic RSI and adds 'Stoch_K' and 'Stoch_K_prev' columns to df."""
    stoch = ta.stochrsi(df['Close'], length=length, rsi_length=rsi_length, k=k, d=d)
    if stoch is not None and f'STOCHRSIk_{length}_{rsi_length}_{k}_{d}' in stoch.columns:
        df['Stoch_K'] = stoch[f'STOCHRSIk_{length}_{rsi_length}_{k}_{d}']
        df['Stoch_K_prev'] = df['Stoch_K'].shift(1)
    return df


def compute_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """Computes Volume-Weighted Average Price and adds 'VWAP' column to df."""
    vwap = ta.vwap(df['High'], df['Low'], df['Close'], df['Volume'])
    if vwap is not None:
        df['VWAP'] = vwap
    return df
