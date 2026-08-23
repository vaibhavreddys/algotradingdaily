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
from strategies.registry import discover_strategies, load_strategy_instance


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

    df_signals = pd.DataFrame(all_signals)
    if 'Entry Time' in df_signals.columns:
        sort_cols = [c for c in ['Entry Time', 'Symbol'] if c in df_signals.columns]
        df_signals = df_signals.sort_values(by=sort_cols, ascending=[True, True]).reset_index(drop=True)
    return df_signals


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
    strategy_id: str = "vwap_stoch_breakdown",
    version: str = "v1_0",
    timeframe: str = "15m",
    universe: str = "ALL",
    capital: float = 100000.0,
    config: TradingConfig = CONFIG, 
    refresh: bool = False, 
    period: str = "60d"
):
    """Main orchestrator for the portfolio simulation."""
    strategy = load_strategy_instance(strategy_id, version)
    if not strategy:
        print(f"❌ Error: Strategy '{strategy_id}:{version}' not found.")
        return

    # Create active config with requested capital
    active_cfg = TradingConfig(
        INITIAL_CAPITAL=capital,
        MAX_CONCURRENT_POSITIONS=config.MAX_CONCURRENT_POSITIONS,
        MAX_RISK_PER_TRADE_PCT=config.MAX_RISK_PER_TRADE_PCT,
        MAX_DAILY_LOSS_PCT=config.MAX_DAILY_LOSS_PCT
    )

    symbols = get_available_symbols(universe=universe)
    nifty_pct_map = fetch_nifty_benchmark(
        period=period,
        interval=timeframe,
        force_refresh=refresh,
        universe=universe
    )
    signals_df = scan_universe_signals(symbols, nifty_pct_map, config=active_cfg, refresh=refresh)

    tdf, ending_capital, total_charges, _, _ = simulate_portfolio_execution(
        signals_df=signals_df,
        config=active_cfg
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
        config=active_cfg,
        dataset_date_range=dataset_date_range,
        strategy_name=strategy.NAME if strategy else STRATEGY_NAME
    )
    print_multi_broker_matrix(
        tdf=tdf,
        initial_capital=active_cfg.INITIAL_CAPITAL,
        config=active_cfg
    )


def run_interactive_wizard() -> dict:
    """Interactive CLI prompt guiding the user through simulation options."""
    print("\n" + "=" * 70)
    print("🚀 AlgoTradingDaily | Portfolio Simulation Wizard")
    print("=" * 70 + "\n")

    strategies = discover_strategies()
    if not strategies:
        print("❌ No strategies found in strategies/ directory.")
        sys.exit(1)

    # 1. Select Strategy
    print("[1/5] Select Strategy:")
    for i, s in enumerate(strategies, 1):
        print(f"  {i}) {s['name']} ({s['id']})")
    choice = input(f"Enter choice [1-{len(strategies)}] (default: 1): ").strip()
    strat_idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(strategies) else 0
    selected_strat = strategies[strat_idx]

    # 2. Select Version
    versions = selected_strat.get("versions", [])
    print(f"\n[2/5] Select Version for {selected_strat['name']}:")
    for i, v in enumerate(versions, 1):
        def_tag = " [default]" if v.get("is_default") else ""
        print(f"  {i}) v{v['version']} ({v['module']}){def_tag}")
    choice = input(f"Enter choice [1-{len(versions)}] (default: 1): ").strip()
    v_idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(versions) else 0
    selected_version = versions[v_idx]["module"] if versions else "v1_0"

    # 3. Select Timeframe
    timeframes = [("15m", "15 minutes (default)"), ("5m", "5 minutes"), ("1m", "1 minute"), ("1h", "1 hour"), ("1d", "1 day")]
    print("\n[3/5] Select Candle Timeframe:")
    for i, (tf_val, tf_lbl) in enumerate(timeframes, 1):
        print(f"  {i}) {tf_lbl}")
    choice = input(f"Enter choice [1-{len(timeframes)}] (default: 1): ").strip()
    tf_idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(timeframes) else 0
    selected_tf = timeframes[tf_idx][0]

    # 4. Select Universe
    universes = [("ALL", "NIFTY 200 (Full DuckDB Store) [default]"), ("NIFTY50", "NIFTY 50 (50 Large-Caps)")]
    print("\n[4/5] Select Stock Universe:")
    for i, (u_val, u_lbl) in enumerate(universes, 1):
        print(f"  {i}) {u_lbl}")
    choice = input(f"Enter choice [1-{len(universes)}] (default: 1): ").strip()
    u_idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(universes) else 0
    selected_universe = universes[u_idx][0]

    # 5. Enter Capital
    print("\n[5/5] Enter Initial Capital (₹):")
    cap_str = input("Capital [default: 100000]: ").strip()
    try:
        selected_capital = float(cap_str) if cap_str else 100000.0
        if selected_capital <= 0:
            selected_capital = 100000.0
    except ValueError:
        selected_capital = 100000.0

    print("\n" + "=" * 70)
    print(f"▶️ Launching: {selected_strat['name']} (v{selected_version}) | {selected_tf} | {selected_universe} | ₹{selected_capital:,.0f}")
    print("=" * 70 + "\n")

    return {
        "strategy_id": selected_strat["id"],
        "version": selected_version,
        "timeframe": selected_tf,
        "universe": selected_universe,
        "capital": selected_capital,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Stock Portfolio Simulation Engine")
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Launch guided interactive CLI wizard to select strategy, version, timeframe, universe, and capital"
    )
    parser.add_argument(
        "--strategy",
        type=str,
        default="vwap_stoch_breakdown",
        help="Strategy ID to simulate (default: vwap_stoch_breakdown)"
    )
    parser.add_argument(
        "--version",
        type=str,
        default="v1_0",
        help="Strategy version module (default: v1_0)"
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default="15m",
        choices=["1m", "5m", "15m", "1h", "1d"],
        help="Candle timeframe to evaluate (default: 15m)"
    )
    parser.add_argument(
        "--universe",
        type=str,
        default="ALL",
        choices=["ALL", "NIFTY50", "NIFTY200"],
        help="Stock universe to simulate (default: ALL / NIFTY200)"
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=100000.0,
        help="Initial trading capital in ₹ (default: 100000.0)"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-download of fresh candles (bypasses local cache)"
    )
    parser.add_argument(
        "--period",
        type=str,
        default="60d",
        help="Historical lookback period when downloading via Yahoo Finance fallback (default: 60d)"
    )
    args = parser.parse_args()

    if args.interactive:
        wizard_cfg = run_interactive_wizard()
        run_portfolio_simulation(
            strategy_id=wizard_cfg["strategy_id"],
            version=wizard_cfg["version"],
            timeframe=wizard_cfg["timeframe"],
            universe=wizard_cfg["universe"],
            capital=wizard_cfg["capital"],
            refresh=args.refresh,
            period=args.period
        )
    else:
        run_portfolio_simulation(
            strategy_id=args.strategy,
            version=args.version,
            timeframe=args.timeframe,
            universe=args.universe,
            capital=args.capital,
            refresh=args.refresh,
            period=args.period
        )
