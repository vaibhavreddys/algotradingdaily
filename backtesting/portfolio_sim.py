"""
Multi-Stock Chronological Portfolio Execution Simulator.

Simulates real-world account execution under realistic trading constraints:
  - Fixed baseline capital (default: ₹10,000)
  - Strict max concurrent position slots (default: 2 slots)
  - Intraday equity MIS leverage (default: 5x)
  - Exact Shoonya statutory taxes and brokerage deductions per trade
  - Chronological slot allocation (first valid breakdown fills open slot)
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
from config import CONFIG, TradingConfig
from data_pipeline import get_nifty50_symbols, fetch_nifty_benchmark, load_candle_data
from strategies.vwap_stoch_breakdown import (
    STRATEGY_NAME,
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
            period=config.BACKTEST_PERIOD,
            interval=config.TIMEFRAME,
            force_refresh=refresh,
            verbose=False,
        )
        if raw_df is None:
            return ticker, []

        df = evaluate_signals(raw_df, nifty_pct_map, config=config)
        if df is None:
            return ticker, []

        signal_positions = np.flatnonzero(df['Signal'].to_numpy())
        signal_indices = signal_positions[signal_positions >= config.SWING_HIGH_BARS].tolist()

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
    Scans all stock symbols in the universe in parallel (8 workers) and compiles
    all candidate trade signals. Returns a DataFrame of all detected signals
    sorted chronologically by Entry Time.
    """
    all_signals = []
    total = len(symbols)
    print(f"[2/3] Scanning Nifty 50 constituents with RELATIVE WEAKNESS filter ({total} symbols, 8 parallel workers)...")

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

        # Collect in original symbol order so the downstream chronological sort
        # sees identical input to a sequential run -> exact numerical parity.
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
    max concurrent position slots and computing exact Shoonya regulatory fee deductions per trade.
    """
    print("[3/3] Running chronological portfolio execution simulation...")
    capital = config.INITIAL_CAPITAL

    active_trades = []
    executed_trades = []
    total_charges_paid = 0.0

    if not signals_df.empty:
        for _, sig in signals_df.iterrows():
            # Release slots that closed before this entry
            active_trades = [t for t in active_trades if t['Exit Time'] > sig['Entry Time']]

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

                executed_trades.append({
                    'Symbol': sig['Symbol'], 'Entry Time': sig['Entry Time'],
                    'Exit Time': sig['Exit Time'], 'PnL %': sig['PnL %'] * 100,
                    'Slot Margin (₹)': slot_margin, 'Exposure (₹)': trade_exposure,
                    'Gross PnL (₹)': raw_pnl, 'Net PnL (₹)': net_pnl,
                    'Capital': capital, 'Result': sig['Result']
                })
                active_trades.append(sig)

    tdf = pd.DataFrame(executed_trades)
    final_slot_margin = get_slot_margin(capital, config.MAX_CONCURRENT_POSITIONS)
    final_exposure = get_slot_exposure(capital, config.MAX_CONCURRENT_POSITIONS, config.LEVERAGE_MIS)
    return tdf, capital, total_charges_paid, final_exposure, final_slot_margin


def print_simulation_report(
    tdf: pd.DataFrame,
    ending_capital: float,
    total_charges: float,
    config: TradingConfig = CONFIG,
    dataset_date_range: Optional[tuple] = None
):
    """Prints a formatted summary dashboard of the portfolio simulation performance."""
    if tdf.empty:
        print("\n⚠️ No trades were executed during this simulation period.")
        return

    initial_capital = config.INITIAL_CAPITAL
    win_count = len(tdf[tdf['Net PnL (₹)'] > 0])
    loss_count = len(tdf[tdf['Net PnL (₹)'] <= 0])
    total_trades = len(tdf)
    win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0
    net_profit = ending_capital - initial_capital
    net_return_pct = (net_profit / initial_capital) * 100
    gross_profit = net_profit + total_charges
    gross_return_pct = (gross_profit / initial_capital) * 100

    if dataset_date_range and dataset_date_range[0] and dataset_date_range[1]:
        start_date, end_date, trading_days = dataset_date_range
    else:
        start_date = pd.to_datetime(tdf['Entry Time']).min().strftime('%Y-%m-%d')
        end_date = pd.to_datetime(tdf['Exit Time']).max().strftime('%Y-%m-%d')
        trading_days = len(pd.to_datetime(tdf['Entry Time']).dt.date.unique())

    # Quantitative Risk & Performance Analytics
    gross_gains = tdf[tdf['Gross PnL (₹)'] > 0]['Gross PnL (₹)'].sum()
    gross_losses = abs(tdf[tdf['Gross PnL (₹)'] <= 0]['Gross PnL (₹)'].sum())
    profit_factor = (gross_gains / gross_losses) if gross_losses > 0 else float('inf')

    # Max Drawdown (MDD) on Capital Curve
    cum_equity = tdf['Capital']
    running_peak = cum_equity.cummax()
    drawdown_series = cum_equity - running_peak
    mdd_val = abs(drawdown_series.min()) if not drawdown_series.empty else 0.0
    # Peak capital at time of MDD trough
    trough_idx = drawdown_series.idxmin() if not drawdown_series.empty else 0
    peak_at_trough = running_peak.loc[trough_idx] if not drawdown_series.empty else initial_capital
    mdd_pct = (mdd_val / peak_at_trough) * 100 if peak_at_trough > 0 else 0.0

    # Trade Expectancy & Averages
    expectancy = net_profit / total_trades if total_trades > 0 else 0.0
    avg_win = gross_gains / win_count if win_count > 0 else 0.0
    avg_loss = gross_losses / loss_count if loss_count > 0 else 0.0

    print("\n=======================================================")
    print(f"      STRATEGY: {STRATEGY_NAME.upper()}")
    print(f"      ₹{initial_capital:,.0f} CAPITAL SIMULATION (MAX {config.MAX_CONCURRENT_POSITIONS} CONCURRENT)   ")
    print("=======================================================")
    print("Data Source            : Local Archives (market_data/)")
    print(f"Simulation Period      : {start_date} to {end_date} ({trading_days} Trading Days)")
    print(f"Initial Capital        : ₹{initial_capital:,.2f}")
    print(f"Max Simultaneous Trades: {config.MAX_CONCURRENT_POSITIONS} Positions (Equal Capital Split)")
    print(f"Intraday MIS Leverage  : {config.LEVERAGE_MIS}x")
    print(f"Total Trades Taken     : {total_trades}")
    print(f"Winning Trades         : {win_count} | Losing Trades: {loss_count}")
    print(f"Win Rate               : {win_rate:.2f}%")
    print("-------------------------------------------------------")
    print(f"Gross Profit (Pre-Tax) : ₹{gross_profit:,.2f} (+{gross_return_pct:.2f}%)")
    print(f"Total Taxes & Fees     : ₹{total_charges:,.2f}")
    print(f"Total Net Profit       : ₹{net_profit:,.2f} (Post-All Charges)")
    print(f"Ending Capital Balance : ₹{ending_capital:,.2f}")
    print(f"Net Return             : {net_return_pct:.2f}%")
    print("-------------------------------------------------------")
    print(f"Profit Factor          : {profit_factor:.2f}")
    print(f"Max Drawdown (MDD)     : ₹{mdd_val:,.2f} (-{mdd_pct:.2f}%)")
    print(f"Trade Expectancy       : +₹{expectancy:.2f} / trade" if expectancy >= 0 else f"Trade Expectancy       : -₹{abs(expectancy):.2f} / trade")
    print(f"Avg Win / Avg Loss     : +₹{avg_win:,.2f} / -₹{avg_loss:,.2f}")
    print("=======================================================\n")
    print("Outcome Distribution:")
    from core.trade_db import EXIT_DISPLAY_LABELS
    for result_name, count in tdf['Result'].value_counts().items():
        pct = (count / total_trades) * 100
        display_label = EXIT_DISPLAY_LABELS.get(result_name, str(result_name))
        print(f"  • {display_label:<22} : {count:>3} trades ({pct:>5.1f}%)")

    # Multi-Broker Friction & Net Return Comparison Matrix
    from core.charges import BROKER_CHARGES_CONFIG
    
    print("\n=======================================================")
    print("       MULTI-BROKER NET PROFIT COMPARISON MATRIX       ")
    print("=======================================================")
    print(f"{'Broker Schedule':<24} | {'Total Taxes/Fees':<16} | {'Net Realized PnL':<16} | {'Net ROI %':<10}")
    print("-----------------------------------------------------------------------------")

    for b_key, b_info in BROKER_CHARGES_CONFIG.items():
        b_sim_charges = 0.0
        b_sim_net_pnl = 0.0
        
        for _, row in tdf.iterrows():
            trade_exp = row.get('Exposure (₹)', config.per_trade_exposure)
            s_turnover = trade_exp
            b_turnover = trade_exp * (1.0 - (row['PnL %'] / 100.0))
            cost = calculate_charges(s_turnover, b_turnover, broker=b_key)
            raw = config.per_trade_exposure * (row['PnL %'] / 100.0)
            b_sim_charges += cost
            b_sim_net_pnl += (raw - cost)

        b_roi = (b_sim_net_pnl / initial_capital) * 100
        sign = "+" if b_sim_net_pnl >= 0 else "-"
        abs_pnl = abs(b_sim_net_pnl)
        print(f"{b_info['name']:<24} | ₹{b_sim_charges:<15,.2f} | {sign}₹{abs_pnl:<14,.2f} | {sign}{abs(b_roi):.2f}%")

    print("=======================================================\n")


def run_portfolio_simulation(config: TradingConfig = CONFIG, refresh: bool = False):
    """Main orchestrator for the portfolio simulation."""
    symbols = get_nifty50_symbols()
    nifty_pct_map = fetch_nifty_benchmark(
        period=config.BACKTEST_PERIOD,
        interval=config.TIMEFRAME,
        force_refresh=refresh
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

    print_simulation_report(
        tdf=tdf,
        ending_capital=ending_capital,
        total_charges=total_charges,
        config=config,
        dataset_date_range=dataset_date_range
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Stock Chronological Portfolio Simulation")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-download of fresh 15m candles (bypasses local market_data/ archives)"
    )
    args = parser.parse_args()
    run_portfolio_simulation(refresh=args.refresh)
