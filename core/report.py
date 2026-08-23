"""
core/report.py
Unified reporting, dashboard presentation, and outcome formatting layer.
Decouples terminal printing and ASCII table rendering from quantitative calculation engines.
"""

import pandas as pd
from typing import Dict, Any, List, Optional, Tuple
from core.trade_db import EXIT_DISPLAY_LABELS
from core.charges import BROKER_CHARGES_CONFIG, calculate_charges


def format_outcome_distribution(counts_series_or_dict: Any, total_trades: int) -> str:
    """
    Renders standardized ASCII outcome distribution with trade counts and percentages.
    Accepts either a pandas Series or a dictionary of outcome counts.
    """
    if total_trades <= 0:
        return "No trades recorded."

    lines = ["Outcome Distribution:"]
    items = counts_series_or_dict.items() if hasattr(counts_series_or_dict, 'items') else []

    for result_name, count in items:
        pct = (count / total_trades) * 100
        display_label = EXIT_DISPLAY_LABELS.get(result_name, str(result_name))
        lines.append(f"  • {display_label:<22} : {count:>3} trades ({pct:>5.1f}%)")

    return "\n".join(lines)


def print_simulation_report(
    tdf: pd.DataFrame,
    ending_capital: float,
    total_charges: float,
    config: Any,
    dataset_date_range: Optional[Tuple[str, str, int]] = None,
    strategy_name: str = "VWAP-STOCH BREAKDOWN"
) -> None:
    """
    Renders formatted portfolio simulation summary including quantitative edge metrics:
    Profit Factor, Max Drawdown (₹/%), Max Equity Runup, Streaks, Expectancy, and Avg Win/Loss.
    Computes date ranges and net/gross profit automatically.
    """
    total_trades = len(tdf)
    initial_capital = config.INITIAL_CAPITAL

    if total_trades == 0:
        print("\n=======================================================")
        print(f"      STRATEGY: {strategy_name.upper()}")
        print(f"      ₹{initial_capital:,.0f} CAPITAL SIMULATION (MAX {config.MAX_CONCURRENT_POSITIONS} CONCURRENT)   ")
        print("=======================================================")
        print(f"Total Trades Taken     : 0 (No signals triggered)")
        print("=======================================================\n")
        return

    win_count = len(tdf[tdf['PnL %'] > 0])
    loss_count = len(tdf[tdf['PnL %'] <= 0])
    win_rate = (win_count / total_trades) * 100

    net_profit = ending_capital - initial_capital
    gross_profit = net_profit + total_charges
    gross_return_pct = (gross_profit / initial_capital) * 100
    net_return_pct = (net_profit / initial_capital) * 100

    if dataset_date_range and dataset_date_range[0] and dataset_date_range[1]:
        start_date, end_date, trading_days = dataset_date_range
    else:
        start_date = pd.to_datetime(tdf['Entry Time']).min().strftime('%Y-%m-%d')
        end_date = pd.to_datetime(tdf['Exit Time']).max().strftime('%Y-%m-%d')
        trading_days = len(pd.to_datetime(tdf['Entry Time']).dt.date.unique())

    gross_gains = tdf[tdf['Gross PnL (₹)'] > 0]['Gross PnL (₹)'].sum()
    gross_losses = abs(tdf[tdf['Gross PnL (₹)'] <= 0]['Gross PnL (₹)'].sum())
    profit_factor = (gross_gains / gross_losses) if gross_losses > 0 else 999.99

    # Max Drawdown (MDD) calculation
    running_peak = tdf['Capital'].cummax()
    drawdown_series = tdf['Capital'] - running_peak
    mdd_val = abs(drawdown_series.min()) if not drawdown_series.empty else 0.0
    trough_idx = drawdown_series.idxmin() if not drawdown_series.empty else 0
    peak_at_trough = running_peak.loc[trough_idx] if not drawdown_series.empty else initial_capital
    mdd_pct = (mdd_val / peak_at_trough) * 100 if peak_at_trough > 0 else 0.0

    # Trade Expectancy & Averages
    expectancy = net_profit / total_trades if total_trades > 0 else 0.0
    avg_win = gross_gains / win_count if win_count > 0 else 0.0
    avg_loss = gross_losses / loss_count if loss_count > 0 else 0.0

    # Streak & Runup Metrics
    largest_win = tdf['Net PnL (₹)'].max() if not tdf.empty else 0.0
    largest_loss = tdf['Net PnL (₹)'].min() if not tdf.empty else 0.0

    cur_win_streak = 0
    max_win_streak = 0
    cur_loss_streak = 0
    max_loss_streak = 0

    if not tdf.empty:
        for pnl in tdf['Net PnL (₹)']:
            if pnl > 0:
                cur_win_streak += 1
                cur_loss_streak = 0
                if cur_win_streak > max_win_streak:
                    max_win_streak = cur_win_streak
            else:
                cur_loss_streak += 1
                cur_win_streak = 0
                if cur_loss_streak > max_loss_streak:
                    max_loss_streak = cur_loss_streak

    # Max Equity Runup (Trough-to-Peak Surge)
    running_trough = tdf['Capital'].cummin() if not tdf.empty else pd.Series([initial_capital])
    runup_series = tdf['Capital'] - running_trough if not tdf.empty else pd.Series([0.0])
    max_runup_val = runup_series.max() if not runup_series.empty else 0.0
    max_runup_pct = (max_runup_val / initial_capital) * 100

    print("\n=======================================================")
    print(f"      STRATEGY: {strategy_name.upper()}")
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
    print(f"Max Equity Runup       : +₹{max_runup_val:,.2f} (+{max_runup_pct:.2f}%)")
    print(f"Win / Loss Streak      : {max_win_streak} Wins / {max_loss_streak} Losses (Max Consecutive)")
    print(f"Largest Win / Loss     : +₹{largest_win:,.2f} / -₹{abs(largest_loss):,.2f}")
    print(f"Trade Expectancy       : +₹{expectancy:.2f} / trade" if expectancy >= 0 else f"Trade Expectancy       : -₹{abs(expectancy):.2f} / trade")
    print(f"Avg Win / Avg Loss     : +₹{avg_win:,.2f} / -₹{avg_loss:,.2f}")
    print("=======================================================\n")
    print(format_outcome_distribution(tdf['Result'].value_counts(), total_trades))


def print_multi_broker_matrix(
    tdf: pd.DataFrame,
    initial_capital: float,
    config: Any
) -> None:
    """
    Renders the statutory Multi-Broker Friction & Net Return Comparison Matrix.
    """
    print("\n=======================================================")
    print("       MULTI-BROKER NET PROFIT COMPARISON MATRIX       ")
    print("=======================================================")
    print(f"{'Broker Schedule':<24} | {'Total Taxes/Fees':<16} | {'Net Realized PnL':<16} | {'Net ROI %':<10}")
    print("-----------------------------------------------------------------------------")

    for b_key, b_info in BROKER_CHARGES_CONFIG.items():
        b_sim_charges = 0.0
        b_sim_net_pnl = 0.0

        gross_pnl = float(tdf['Gross PnL (₹)'].sum()) if 'Gross PnL (₹)' in tdf.columns else float(tdf['PnL (₹)'].sum())
        for _, row in tdf.iterrows():
            trade_exp = float(row.get('Exposure (₹)', row.get('Exposure', config.INITIAL_CAPITAL / config.MAX_CONCURRENT_POSITIONS * config.LEVERAGE_MIS)))
            pnl_val = float(row.get('Gross PnL (₹)', row.get('PnL (₹)', 0.0)))
            s_turnover = trade_exp
            b_turnover = max(0.0, trade_exp - pnl_val)
            cost = calculate_charges(s_turnover, b_turnover, broker=b_key)
            b_sim_charges += cost
        
        b_sim_net_pnl = gross_pnl - b_sim_charges

        b_roi = (b_sim_net_pnl / initial_capital) * 100
        sign = "+" if b_sim_net_pnl >= 0 else "-"
        abs_pnl = abs(b_sim_net_pnl)
        print(f"{b_info['name']:<24} | ₹{b_sim_charges:<15,.2f} | {sign}₹{abs_pnl:<14,.2f} | {sign}{abs(b_roi):.2f}%")

    print("=======================================================\n")


def print_daily_eod_report(
    day_trades: List[Dict[str, Any]],
    initial_capital: float,
    ending_balance: float,
    date_str: str
) -> Tuple[str, List[str]]:
    """
    Renders daily EOD session performance report and returns (summary_report_text, trade_lines).
    """
    print("\n=====================================================")
    print("         DAILY EOD PERFORMANCE REPORT (PAPER TRADING)")
    print("=====================================================")
    print(f"Date                 : {date_str}")
    print(f"Initial Balance      : ₹{initial_capital:,.2f}")

    if not day_trades:
        print("Total Trades Taken   : 0 (No trades recorded for this date)")
        print("=====================================================\n")
        summary_text = (
            f"📊 Daily Paper Trading Summary ({date_str})\n"
            f"Trades Taken: 0 | Session Closed Cleanly."
        )
        return summary_text, []

    total_trades = len(day_trades)
    winning_trades = [t for t in day_trades if t.get('net_pnl', 0) > 0]
    losing_trades = [t for t in day_trades if t.get('net_pnl', 0) <= 0]
    win_count = len(winning_trades)
    loss_count = len(losing_trades)
    win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0.0

    gross_pnl = sum(t.get('gross_pnl', 0.0) for t in day_trades)
    taxes_fees = sum(t.get('taxes_fees', 0.0) for t in day_trades)
    net_pnl = sum(t.get('net_pnl', 0.0) for t in day_trades)
    roi_pct = (net_pnl / initial_capital) * 100

    print(f"Total Trades Taken   : {total_trades} ({win_count} Wins / {loss_count} Losses)")
    print(f"Win Rate             : {win_rate:.1f}%")
    print("-----------------------------------------------------")
    print(f"Gross Realized PnL   : {'+' if gross_pnl >= 0 else '-'}₹{abs(gross_pnl):,.2f}")
    print(f"Simulated Taxes/Fees : -₹{taxes_fees:,.2f}")
    print(f"Net Realized PnL     : {'+' if net_pnl >= 0 else '-'}₹{abs(net_pnl):,.2f} (Post-All Charges)")
    print(f"Ending Balance       : ₹{ending_balance:,.2f} ({'+' if roi_pct >= 0 else ''}{roi_pct:.2f}% Daily ROI)")
    print("=====================================================")
    print("Trade Log:")
    
    trade_lines = []
    for idx, t in enumerate(day_trades, 1):
        sym = t.get('symbol', 'UNKNOWN')
        ep = t.get('entry_price', 0.0)
        xp = t.get('exit_price', 0.0)
        raw_res = t.get('result', '')
        res = EXIT_DISPLAY_LABELS.get(raw_res, raw_res)
        npnl = t.get('net_pnl', 0.0)
        line = f"{idx}. {sym:<14}: SHORT @ ₹{ep:,.2f} -> {res} @ ₹{xp:,.2f} | Net: {'+' if npnl >= 0 else '-'}₹{abs(npnl):,.2f}"
        trade_lines.append(line)
        print(line)

    print("=====================================================")
    print("STATUS: 🔒 All positions squared off. Session closed.")
    print("=====================================================\n")

    summary_msg = (
        f"📊 *Daily Paper Trading Summary ({date_str})*\n\n"
        f"• *Total Trades*: {total_trades} ({win_count}W / {loss_count}L | Win Rate: {win_rate:.1f}%)\n"
        f"• *Gross PnL*: `{'₹+' if gross_pnl >= 0 else '₹-'}{abs(gross_pnl):,.2f}`\n"
        f"• *Taxes & Charges*: `₹{taxes_fees:,.2f}`\n"
        f"• *Net Realized PnL*: `{'₹+' if net_pnl >= 0 else '₹-'}{abs(net_pnl):,.2f}` (`{'+' if roi_pct >= 0 else ''}{roi_pct:.2f}%`)\n"
        f"• *Ending Account Capital*: `₹{ending_balance:,.2f}`\n\n"
        f"*Trade Breakdown:*\n" + "\n".join(trade_lines)
    )

    return summary_msg, trade_lines
