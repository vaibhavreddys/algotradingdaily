"""
Multi-Stock Chronological Portfolio Execution Simulator.

Simulates real-world account execution under realistic trading constraints:
  - Dynamic compounding account equity
  - Strict max concurrent position slots (default: 2 slots)
  - Intraday equity MIS leverage (default: 5x)
  - Statutory taxes and multi-broker friction modeling per trade
  - Chronological slot allocation (first valid breakdown fills open slot)
  - Intraday daily loss circuit breaker protection
"""

import os
import sys
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Tuple
import numpy as np
import pandas as pd

# Ensure workspace root is in sys.path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from core.charges import calculate_charges
from core.capital import get_slot_margin, get_slot_exposure
from core.risk import is_daily_loss_limit_reached
from core.report import print_simulation_report, print_multi_broker_matrix
from config import CONFIG, TradingConfig
from data_pipeline import get_nifty50_symbols, get_available_symbols, fetch_nifty_benchmark, load_candle_data
from strategies.vwap_stoch_breakdown import (
    STRATEGY_NAME,
    TIMEFRAME,
    SWING_HIGH_BARS,
    evaluate_signals,
    simulate_single_trade,
)


def _scan_single_symbol(ticker, nifty_pct_map, config: TradingConfig, refresh: bool):
    """
    Worker: loads candles, evaluates indicators, and simulates all trades for one symbol.
    Returns (ticker, trades). Owns its DataFrame exclusively -> thread-safe.
    """
    try:
        raw_df = load_candle_data(
            ticker,
            period=getattr(config, 'BACKTEST_PERIOD', '60d'),
            interval=getattr(config, 'TIMEFRAME', TIMEFRAME),
            force_refresh=refresh,
            verbose=False,
        )
        if raw_df is None:
            return ticker, []

        df = evaluate_signals(raw_df, nifty_pct_map, config=config)
        if df is None:
            return ticker, []

        signal_positions = np.flatnonzero(df['Signal'].to_numpy())
        swing_bars = getattr(config, 'SWING_HIGH_BARS', SWING_HIGH_BARS)
        signal_indices = signal_positions[signal_positions >= swing_bars].tolist()

        trades = []
        for i in signal_indices:
            trade = simulate_single_trade(df, i, ticker, config=config)
            if trade:
                trades.append(trade)
        return ticker, trades
    except Exception:
        return ticker, []


def scan_universe_signals(symbols, nifty_pct_map, config: TradingConfig = CONFIG, refresh: bool = False):
    """
    Scans all stock symbols in parallel and compiles candidate trade signals sorted chronologically.
    """
    all_signals = []
    total = len(symbols)
    print(f"[2/3] Scanning {total} universe constituents with RELATIVE WEAKNESS filter (8 parallel workers)...")

    if not symbols:
        return pd.DataFrame()

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            ticker: executor.submit(_scan_single_symbol, ticker, nifty_pct_map, config, refresh)
            for ticker in symbols
        }

        done = 0
        for _ in as_completed(futures.values()):
            done += 1
            if done % 5 == 0 or done == total:
                print(f"      ✓ {done}/{total} symbols scanned", end="\r")
        print()

        for ticker in symbols:
            _, trades = futures[ticker].result()
            if trades:
                all_signals.extend(trades)

    if not all_signals:
        return pd.DataFrame()

    return pd.DataFrame(all_signals).sort_values(by='Entry Time').reset_index(drop=True)


def simulate_portfolio_execution(signals_df: pd.DataFrame, config: TradingConfig = CONFIG):
    """
    Executes candidate signals chronologically, enforcing dynamic equal-split compounding across
    max concurrent position slots and computing exact regulatory fee deductions per trade.
    """
    print("[3/3] Running chronological portfolio execution simulation...")
    capital = config.INITIAL_CAPITAL

    active_trades = []
    executed_trades = []
    total_charges_paid = 0.0

    current_sim_day = None
    day_starting_capital = capital
    day_realized_pnl = 0.0

    if not signals_df.empty:
        for _, sig in signals_df.iterrows():
            sig_day = pd.to_datetime(sig['Entry Time']).date()

            # Daily Session Reset
            if sig_day != current_sim_day:
                current_sim_day = sig_day
                day_starting_capital = capital
                day_realized_pnl = 0.0

            # Release slots that closed before this entry and tally day realized PnL in a single pass
            still_active = []
            for t in active_trades:
                if t['Exit Time'] <= sig['Entry Time']:
                    day_realized_pnl += t['net_pnl']
                else:
                    still_active.append(t)
            active_trades = still_active

            # Enforce Daily Max Portfolio Loss Circuit Breaker (e.g. 4%)
            if is_daily_loss_limit_reached(day_realized_pnl, day_starting_capital, config.MAX_DAILY_LOSS_PCT):
                continue

            if len(active_trades) < config.MAX_CONCURRENT_POSITIONS:
                # Dynamically split CURRENT accumulated capital equally across configured slots
                slot_margin = get_slot_margin(capital, config.MAX_CONCURRENT_POSITIONS)
                trade_exposure = get_slot_exposure(capital, config.MAX_CONCURRENT_POSITIONS, config.LEVERAGE_MIS)

                sell_turnover = trade_exposure
                buy_turnover = trade_exposure * (1.0 - sig['PnL %'])

                trade_cost = calculate_charges(sell_turnover, buy_turnover)
                total_charges_paid += trade_cost

                raw_pnl = trade_exposure * sig['PnL %']
                net_pnl = raw_pnl - trade_cost
                capital += net_pnl

                t_dict = {
                    'Symbol': sig['Symbol'], 'Entry Time': sig['Entry Time'],
                    'Exit Time': sig['Exit Time'], 'PnL %': sig['PnL %'] * 100,
                    'Slot Margin (₹)': slot_margin, 'Exposure (₹)': trade_exposure,
                    'Gross PnL (₹)': raw_pnl, 'Net PnL (₹)': net_pnl,
                    'net_pnl': net_pnl,
                    'Capital': capital, 'Result': sig['Result']
                }
                executed_trades.append(t_dict)
                active_trades.append(t_dict)

    tdf = pd.DataFrame(executed_trades)
    final_slot_margin = get_slot_margin(capital, config.MAX_CONCURRENT_POSITIONS)
    final_exposure = get_slot_exposure(capital, config.MAX_CONCURRENT_POSITIONS, config.LEVERAGE_MIS)
    return tdf, capital, total_charges_paid, final_exposure, final_slot_margin


def run_portfolio_simulation(
    config: TradingConfig = CONFIG, 
    refresh: bool = False, 
    universe: str = "ALL",
    period: str = "60d"
):
    """Main orchestrator for the portfolio simulation."""
    symbols = get_available_symbols(universe=universe)
    nifty_pct_map = fetch_nifty_benchmark(
        period=getattr(config, 'BACKTEST_PERIOD', '60d'),
        interval=getattr(config, 'TIMEFRAME', TIMEFRAME),
        force_refresh=refresh,
        universe=universe
    )
    signals_df = scan_universe_signals(symbols, nifty_pct_map, config=config, refresh=refresh)

    tdf, ending_capital, total_charges, _, _ = simulate_portfolio_execution(
        signals_df=signals_df,
        config=config
    )

    dataset_date_range = None
    if nifty_pct_map is not None and not nifty_pct_map.empty:
        idx_dt = pd.to_datetime(nifty_pct_map.index)
        bench_start = idx_dt.min().strftime('%Y-%m-%d')
        bench_end = idx_dt.max().strftime('%Y-%m-%d')
        bench_days = len(set(idx_dt.date))
        dataset_date_range = (bench_start, bench_end, bench_days)

    # Render comprehensive performance dashboard & multi-broker matrix via core.report
    print_simulation_report(
        tdf=tdf,
        ending_capital=ending_capital,
        total_charges=total_charges,
        config=config,
        dataset_date_range=dataset_date_range,
        strategy_name=STRATEGY_NAME
    )
    print_multi_broker_matrix(
        tdf=tdf,
        initial_capital=config.INITIAL_CAPITAL,
        config=config
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Stock Portfolio Simulation Engine")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-download of fresh 15m candles (bypasses local market_data/ archives)"
    )
    parser.add_argument(
        "--universe",
        type=str,
        default="ALL",
        choices=["ALL", "NIFTY50", "NIFTY200"],
        help="Stock universe to simulate (default: ALL 200 constituents)"
    )
    parser.add_argument(
        "--period",
        type=str,
        default="60d",
        help="Historical lookback period when downloading via Yahoo Finance fallback (default: 60d)"
    )
    args = parser.parse_args()
    run_portfolio_simulation(refresh=args.refresh, universe=args.universe, period=args.period)
