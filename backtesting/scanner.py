"""
Standalone Single-Stock Technical Indicator Scanner.

Scans the NIFTY 50 universe unconstrained (assuming unlimited capital slots)
using the VWAP-Stoch Breakdown strategy from the strategies package.
"""

import os
import sys
import time
import argparse
import pandas as pd

# Ensure workspace root is in sys.path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from config import CONFIG, TradingConfig
from data_pipeline import get_nifty50_symbols, fetch_nifty_benchmark
from strategies.vwap_stoch_breakdown import STRATEGY_NAME, STRATEGY_VERSION
from backtesting.portfolio_sim import scan_universe_signals


def run_strategy_scan(config: TradingConfig = CONFIG, refresh: bool = False):
    symbols = get_nifty50_symbols()
    start_time = time.time()

    print("\n=======================================================")
    print(f"  SCANNER: {STRATEGY_NAME.upper()} (v{STRATEGY_VERSION})")
    print(f"  NIFTY 50 ({config.ENTRY_START_HOUR}:00 AM - {config.ENTRY_END_HOUR}:{config.ENTRY_END_MINUTE} | {config.SWING_HIGH_BARS}-Bar Swing High SL)  ")
    print("=======================================================")

    nifty_pct_map = fetch_nifty_benchmark(
        period=config.BACKTEST_PERIOD,
        interval=getattr(config, 'TIMEFRAME', TIMEFRAME),
        force_refresh=refresh
    )
    signals_df = scan_universe_signals(symbols, nifty_pct_map, config=config, refresh=refresh)

    elapsed = time.time() - start_time
    if signals_df.empty:
        print("No trades triggered with these rules.")
        return

    tdf = signals_df.copy()
    tdf['PnL %'] = tdf['PnL %'] * 100

    if tdf.empty:
        print("No trades triggered with these rules.")
        return

    total = len(tdf)
    wins = len(tdf[tdf['PnL %'] > 0])
    losses = total - wins
    win_rate = (wins / total) * 100
    net_pnl = tdf['PnL %'].sum()
    avg_trade_pnl = tdf['PnL %'].mean()

    start_date = pd.to_datetime(tdf['Entry Time']).min().strftime('%Y-%m-%d')
    end_date = pd.to_datetime(tdf['Exit Time']).max().strftime('%Y-%m-%d')

    print("\n=======================================================")
    print(f"      {STRATEGY_NAME.upper()} SCANNER RESULTS          ")
    print("=======================================================")
    print(f"Scan Period            : {start_date} to {end_date}")
    print(f"Total Universe Scanned : {len(symbols)} Stocks")
    print(f"Execution Time         : {elapsed:.1f} seconds")
    print(f"Total Trades Generated : {total}")
    print(f"Winning Trades         : {wins}")
    print(f"Losing Trades          : {losses}")
    print(f"Win Rate               : {win_rate:.2f}%")
    print(f"Avg Return / Trade     : {avg_trade_pnl:.3f}%")
    print(f"Cumulative Return      : {net_pnl:.2f}% (Unleveraged, Unconstrained)")
    print("=======================================================\n")
    from core.report import format_outcome_distribution
    print(format_outcome_distribution(tdf['Result'].value_counts(), total))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unconstrained Single-Stock Strategy Scanner")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-download of fresh 15m candles (bypasses local market_data/ archives)"
    )
    args = parser.parse_args()
    run_strategy_scan(refresh=args.refresh)
