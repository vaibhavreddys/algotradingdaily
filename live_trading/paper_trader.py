"""
Live Trading Paper Execution Engine.

Executes real-time strategy signals in virtual paper trading mode:
  - Fills orders virtually at prevailing market prices (zero real capital risk)
  - Tracks live position lifecycles (SL hit, 1:2 Target hit, +1R trailing SL to BE, 3:00 PM Exit)
  - Emits real-time trade logs and updates virtual PnL
"""

import os
import sys
import time
import datetime
from typing import Dict, Any, Optional, List
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
from live_trading.base_engine import BaseTradingEngine
from core.capital import calculate_order_quantity
from strategies.vwap_stoch_breakdown import STRATEGY_NAME, evaluate_signals
from data_pipeline import (
    get_nifty50_symbols,
    fetch_nifty_benchmark,
    fetch_stock_candles,
    fetch_verified_candles,
    fetch_latest_tick_price,
)
from core.trade_db import (
    save_active_position,
    update_trailing_sl,
    close_and_archive_position,
    get_active_positions,
    TradeExitReason,
    EXIT_DISPLAY_LABELS,
)
from alerts import notify_trade_entry, notify_trailing_sl, notify_trade_exit, notify_eod_summary


class PaperTradingEngine(BaseTradingEngine):
    """
    Simulates real-time market execution without placing orders on exchange.
    Used for live forward validation of strategy signals.
    """
    def __init__(self, config: TradingConfig = CONFIG):
        super().__init__(config=config)
        self.virtual_balance = self.get_account_capital()
        self.paper_trades = []

    def execute_virtual_entry(self, symbol: str, entry_price: float, sl_price: float, tp_price: float):
        """Simulates virtual entry and establishes stop loss / target in SQLite database."""
        if len(self.active_positions) >= self.config.MAX_CONCURRENT_POSITIONS:
            return

        qty = calculate_order_quantity(
            entry_price=entry_price,
            current_capital=self.virtual_balance,
            max_concurrent_positions=self.config.MAX_CONCURRENT_POSITIONS,
            leverage_mis=self.config.LEVERAGE_MIS
        )
        risk = sl_price - entry_price
        entry_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        order_id = f"PAPER_ORD_{int(time.time())}"
        sl_order_id = f"PAPER_SL_{int(time.time())}"

        self.active_positions[symbol] = {
            'symbol': symbol,
            'entry_price': entry_price,
            'entry_time': entry_time,
            'qty': qty,
            'sl_price': sl_price,
            'tp_price': tp_price,
            'risk': risk,
            'trailed': False
        }

        # Persist to database/paper_trades.db for crash recovery
        save_active_position(
            symbol=symbol,
            entry_order_id=order_id,
            sl_order_id=sl_order_id,
            qty=qty,
            entry_p=entry_price,
            sl_p=sl_price,
            tp_p=tp_price,
            mode="paper"
        )
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 📝 [PAPER ENTRY] Short {qty}x {symbol} @ ₹{entry_price:.2f} | SL: ₹{sl_price:.2f} | TP: ₹{tp_price:.2f}")
        notify_trade_entry(symbol=symbol, price=entry_price, sl=sl_price, tp=tp_price, qty=qty, mode="paper", config=self.config)

    def update_position(self, symbol: str, current_ltp: float, high: float, low: float, now: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
        """Updates virtual position tracking against live market ticks."""
        if symbol not in self.active_positions:
            return None

        pos = self.active_positions[symbol]
        entry_p = pos['entry_price']
        curr_sl = pos['sl_price']
        tp = pos['tp_price']
        risk = pos['risk']

        # 1. Stop Loss Trigger
        if high >= curr_sl:
            result = TradeExitReason.TRAILING_SL_HIT if pos['trailed'] else TradeExitReason.SL_HIT
            exit_price = curr_sl
            return self._close_position(symbol, exit_price, result)

        # 2. Target Trigger
        if low <= tp:
            result = TradeExitReason.TARGET_HIT
            exit_price = tp
            return self._close_position(symbol, exit_price, result)

        # 3. Trail to Breakeven at +1R
        if not pos['trailed'] and low <= (entry_p - risk):
            pos['sl_price'] = entry_p
            pos['trailed'] = True
            update_trailing_sl(symbol, new_sl_price=entry_p, mode="paper")
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🛡️ [PAPER TRAIL] {symbol} reached +1R profit! SL moved to Breakeven (₹{entry_p:.2f}).")
            notify_trailing_sl(symbol=symbol, be_price=entry_p, mode="paper", config=self.config)

        # 4. Mandatory Squareoff
        if self.is_squareoff_time(now=now):
            return self._close_position(symbol, current_ltp, TradeExitReason.ALGO_SQUAREOFF_DAY_END)

        return None

    def _close_position(self, symbol: str, exit_price: float, result: str, exit_time: Optional[str] = None) -> Dict[str, Any]:
        """Closes virtual position, computes fees, logs result, and archives to SQLite database."""
        from core.charges import calculate_charges
        from core.trade_db import close_and_archive_position, EXIT_DISPLAY_LABELS

        pos = self.active_positions.pop(symbol)
        entry_p = pos['entry_price']
        qty = pos['qty']
        
        gross_pnl = (entry_p - exit_price) * qty
        sell_turnover = entry_p * qty
        buy_turnover = exit_price * qty
        charges = calculate_charges(sell_turnover=sell_turnover, buy_turnover=buy_turnover)
        net_pnl = gross_pnl - charges
        pnl_pct = (pos['entry_price'] - exit_price) / pos['entry_price'] * 100
        actual_exit_time = exit_time or datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Archive atomically to database/paper_trades.db with clean enum
        close_and_archive_position(
            symbol=symbol,
            exit_price=exit_price,
            exit_time=actual_exit_time,
            result=result,
            gross_pnl=gross_pnl,
            taxes_fees=charges,
            net_pnl=net_pnl,
            mode="paper"
        )

        display_result = EXIT_DISPLAY_LABELS.get(result, result)
        trade_record = {
            'symbol': symbol,
            'entry_time': pos['entry_time'],
            'exit_time': actual_exit_time,
            'entry_price': pos['entry_price'],
            'exit_price': exit_price,
            'qty': pos['qty'],
            'gross_pnl': gross_pnl,
            'taxes_fees': charges,
            'net_pnl': net_pnl,
            'pnl_pct': pnl_pct,
            'result': result
        }
        self.paper_trades.append(trade_record)
        self.virtual_balance = round(self.virtual_balance + net_pnl, 2)
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🏁 [PAPER EXIT] {symbol} @ ₹{exit_price:.2f} | Net PnL: ₹{net_pnl:+.2f} ({pnl_pct:+.2f}%) | {display_result}")
        notify_trade_exit(symbol=symbol, price=exit_price, net_pnl=net_pnl, pnl_pct=pnl_pct, reason=display_result, mode="paper", config=self.config)
        return trade_record

    def generate_eod_report(self) -> None:
        """
        Queries database/paper_trades.db and generates a formatted End-Of-Day (EOD) Performance Report for today's session.
        """
        from core.trade_db import get_trade_journal

        today_date = datetime.datetime.now().strftime('%Y-%m-%d')
        all_trades = get_trade_journal(mode="paper", limit=500)
        
        # Filter trades for today's session
        day_trades = [t for t in all_trades if str(t.get('exit_time', '')).startswith(today_date)]

        print("\n=====================================================")
        print("         DAILY EOD PERFORMANCE REPORT (PAPER TRADING)")
        print("=====================================================")
        print(f"Date                 : {today_date}")
        print(f"Initial Balance      : ₹{self.config.INITIAL_CAPITAL:,.2f}")

        if not day_trades:
            print("Total Trades Taken   : 0 (No trades recorded for this date)")
            print("=====================================================\n")
            return

        total_trades = len(day_trades)
        winning_trades = [t for t in day_trades if t.get('net_pnl', 0) > 0]
        losing_trades = [t for t in day_trades if t.get('net_pnl', 0) <= 0]
        win_count = len(winning_trades)
        loss_count = len(losing_trades)
        win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0.0

        gross_pnl = sum(t.get('gross_pnl', 0.0) for t in day_trades)
        taxes_fees = sum(t.get('taxes_fees', 0.0) for t in day_trades)
        net_pnl = sum(t.get('net_pnl', 0.0) for t in day_trades)
        ending_balance = get_persisted_paper_capital(self.config.INITIAL_CAPITAL, mode='paper')
        roi_pct = (net_pnl / self.config.INITIAL_CAPITAL) * 100

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
        from core.trade_db import EXIT_DISPLAY_LABELS
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
        print("STATUS: 🏁 All positions squared off. Session closed.")
        print("=====================================================\n")

        # Broadcast summary to Telegram & configured alert channels
        eod_msg = (
            f"Date: {today_date}\n"
            f"Trades: {total_trades} ({win_count}W / {loss_count}L) | Win Rate: {win_rate:.1f}%\n"
            f"Gross PnL: {'+' if gross_pnl >= 0 else '-'}₹{abs(gross_pnl):,.2f}\n"
            f"Taxes/Fees: -₹{taxes_fees:,.2f}\n"
            f"Net PnL: {'+' if net_pnl >= 0 else '-'}₹{abs(net_pnl):,.2f} ({roi_pct:+.2f}% ROI)\n"
            f"Ending Balance: ₹{ending_balance:,.2f}\n\n"
            f"Trade Log:\n" + "\n".join(trade_lines)
        )
        notify_eod_summary(report_text=eod_msg, mode="paper", config=self.config)


    def scan_and_execute_signals(self, nifty_pct_map: pd.Series) -> None:
        """
        Scans the Nifty 50 universe at 15m candle close and triggers virtual entries if open slots exist.
        """
        if len(self.active_positions) >= self.config.MAX_CONCURRENT_POSITIONS:
            return

        symbols = get_nifty50_symbols()
        for ticker in symbols:
            if len(self.active_positions) >= self.config.MAX_CONCURRENT_POSITIONS:
                break

            sym_key = f"{ticker.replace('.NS', '')}-EQ"
            if sym_key in self.active_positions or ticker in self.active_positions:
                continue

            try:
                raw_df = fetch_verified_candles(ticker, period="5d", interval=self.config.TIMEFRAME)
                if raw_df is None or len(raw_df) < (self.config.SWING_HIGH_BARS + 5):
                    continue

                df = evaluate_signals(raw_df, nifty_pct_map, config=self.config)
                if df is None or len(df) == 0:
                    continue

                last_idx = len(df) - 1
                last_row = df.iloc[last_idx]

                # Check if breakdown signal fired on the latest closed candle
                if last_row.get('Signal', False):
                    entry_price = round(float(last_row['Close']), 2)
                    swing_high = float(df.iloc[last_idx - self.config.SWING_HIGH_BARS : last_idx]['High'].max())
                    sl_price = round(max(
                        swing_high * (1.0 + self.config.SWING_SL_BUFFER_PCT),
                        entry_price * (1.0 + self.config.MIN_SL_BUFFER_PCT)
                    ), 2)
                    risk = round(sl_price - entry_price, 2)
                    tp_price = round(entry_price - (2.0 * risk), 2)

                    self.execute_virtual_entry(
                        symbol=sym_key,
                        entry_price=entry_price,
                        sl_price=sl_price,
                        tp_price=tp_price
                    )
            except Exception:
                continue

    def monitor_active_positions(self) -> None:
        """
        High-Frequency Position Guardian:
        Checks active positions against latest 1m candle ticks and triggers instant SL, TP, or +1R Trailing SL.
        """
        if not self.active_positions:
            return

        for symbol in list(self.active_positions.keys()):
            ticker = f"{symbol.replace('-EQ', '')}.NS"
            try:
                tick = fetch_latest_tick_price(ticker)
                if tick is None:
                    continue
                ltp = tick['ltp']
                high = tick['high']
                low = tick['low']

                self.update_position(symbol=symbol, current_ltp=ltp, high=high, low=low)
            except Exception:
                continue

    def squareoff_all_positions(self) -> None:
        """
        Mandatory 3:00 PM square-off for all remaining open virtual positions.
        Resolves accurate exit price using high-frequency 1m/5m live tick feeds.
        """
        if not self.active_positions:
            return

        try:
            print(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] ⏱️ MANDATORY 3:00 PM AUTO-SQUAREOFF ENFORCED.")
        except Exception:
            print(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] [MANDATORY 3:00 PM AUTO-SQUAREOFF ENFORCED.]")

        for symbol in list(self.active_positions.keys()):
            ticker = f"{symbol.replace('-EQ', '')}.NS"
            ltp = self.active_positions[symbol]['entry_price']
            try:
                tick = fetch_latest_tick_price(ticker)
                if tick is not None and tick.get('ltp'):
                    ltp = tick['ltp']
            except Exception:
                pass

            try:
                self._close_position(symbol, exit_price=ltp, result=TradeExitReason.ALGO_SQUAREOFF_DAY_END)
            except Exception:
                pass

    def run_live_loop(self) -> None:
        """
        Continuous live execution daemon.
        - Strategy Scans (Macro Loop): Runs strictly on 15m candle close boundaries (:00, :15, :30, :45).
        - Position Guardian (Micro Loop): Polls active positions every 15s for instant SL/TP/Trailing.
        - Enforces 15:00 squareoff and prints EOD report on market close.
        """
        print(f"\n=======================================================")
        print(f"       PAPER TRADING ENGINE: {STRATEGY_NAME}")
        print(f"       Capital: ₹{self.config.INITIAL_CAPITAL:,.0f} | Max Slots: {self.config.MAX_CONCURRENT_POSITIONS}")
        print(f"       Scanning: 15m Candle Closes | Guardian: {self.config.POSITION_MONITOR_INTERVAL_SEC}s Ticks")
        print(f"=======================================================\n")

        # 1. Authenticate & restore SQLite state
        self.authenticate()
        self.sync_active_positions_from_db(mode="paper")

        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚀 Live Paper Trading Engine started.")

        try:
            while True:
                now = datetime.datetime.now()

                # Check if market has officially closed for the day
                if self.is_market_closed(now):
                    print(f"[{now.strftime('%H:%M:%S')}] 🛑 Market is closed for today.")
                    if self.active_positions:
                        self.squareoff_all_positions()
                    self.generate_eod_report()
                    break

                # If before market open (09:15 AM), wait until 09:15
                if not self.is_market_open(now):
                    print(f"[{now.strftime('%H:%M:%S')}] ⏳ Pre-market: Waiting for market open at 09:15 AM IST...")
                    time.sleep(30)
                    continue

                # 2. Check 3:00 PM Squareoff
                if self.is_squareoff_time(now):
                    if self.active_positions:
                        self.squareoff_all_positions()
                    self.generate_eod_report()
                    print(f"[{now.strftime('%H:%M:%S')}] 🏁 Trading session completed for today.")
                    break

                # 3. Strategy Entry Window Scan (10:00 AM - 1:30 PM on 15m Candle Closes)
                if self.is_entry_window_active(now):
                    if len(self.active_positions) < self.config.MAX_CONCURRENT_POSITIONS:
                        nifty_pct_map = self.get_benchmark_feed()
                        self.scan_and_execute_signals(nifty_pct_map)

                # 4. Non-Blocking High-Frequency Guardian Loop:
                # Interleaves 15-second active position checks while waiting for next 15m candle close
                wait_sec = self.get_seconds_until_next_candle(interval_mins=15, now=now)
                next_check = (now + datetime.timedelta(seconds=wait_sec)).strftime('%H:%M:%S')
                print(f"[{now.strftime('%H:%M:%S')}] 💤 Next 15m scan in {wait_sec}s ({next_check}). Active slots: {len(self.active_positions)}/{self.config.MAX_CONCURRENT_POSITIONS}")

                # Poll active positions every POSITION_MONITOR_INTERVAL_SEC seconds until next candle boundary
                poll_interval = self.config.POSITION_MONITOR_INTERVAL_SEC
                target_wake_time = time.time() + wait_sec
                prewarmed = False

                while time.time() < target_wake_time:
                    remaining_time = target_wake_time - time.time()

                    # Pre-warm benchmark feed ~5s before next candle close for zero-latency scanning
                    if remaining_time <= 5.0 and not prewarmed and self.is_entry_window_active(datetime.datetime.now()):
                        try:
                            self.prewarm_benchmark_feed()
                            prewarmed = True
                        except Exception:
                            pass

                    sleep_chunk = min(poll_interval, remaining_time)
                    if sleep_chunk > 0:
                        time.sleep(sleep_chunk)

                    # High-frequency guardian check for active positions
                    if self.active_positions:
                        self.monitor_active_positions()

                    # Re-check 3:00 PM squareoff during polling
                    curr_time = datetime.datetime.now()
                    if self.is_squareoff_time(curr_time):
                        break

        except KeyboardInterrupt:
            print("\n⚠️ User interrupted live loop (Ctrl+C). Generating current session report...")
            self.generate_eod_report()


if __name__ == "__main__":
    engine = PaperTradingEngine()
    engine.run_live_loop()
