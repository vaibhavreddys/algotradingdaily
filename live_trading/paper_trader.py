from data_pipeline import fetch_latest_tick_price
"""
Live Paper Trading Execution Engine.

Simulates real-time market execution without placing orders on exchange:
  - Fills orders virtually at prevailing market prices (zero real capital risk)
  - Tracks live position lifecycles (SL hit, 1:2 Target hit, +1R trailing SL to BE, 3:00 PM Exit)
  - Persists state to database/paper_trades.db and emits real-time Telegram alerts
"""

import os
import sys
import time
import datetime
from typing import Dict, Any, Optional

# Ensure workspace root is in sys.path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import CONFIG, TradingConfig
from live_trading.base_engine import BaseTradingEngine, prevent_sleep_context
from core.capital import calculate_order_quantity
from core.charges import calculate_charges
from core.trade_db import (
    save_active_position,
    update_trailing_sl,
    close_and_archive_position,
    TradeExitReason,
    EXIT_DISPLAY_LABELS,
)
from alerts import notify_trade_entry, notify_trailing_sl, notify_trade_exit


class PaperTradingEngine(BaseTradingEngine):
    """
    Simulates real-time market execution without placing orders on exchange.
    Used for live forward validation of strategy signals.
    """
    def __init__(self, config: TradingConfig = CONFIG):
        super().__init__(config=config, mode="paper")
        self.paper_trades = self.closed_trades

    def execute_entry(self, symbol: str, entry_price: float, sl_price: float, tp_price: float) -> bool:
        """Simulates virtual entry and establishes stop loss / target in SQLite database."""
        if len(self.active_positions) >= self.config.MAX_CONCURRENT_POSITIONS:
            return False

        qty = calculate_order_quantity(
            entry_price=entry_price,
            current_capital=self.get_account_capital(),
            max_concurrent_positions=self.config.MAX_CONCURRENT_POSITIONS,
            leverage_mis=self.config.LEVERAGE_MIS,
            sl_price=sl_price,
            max_risk_pct=self.config.MAX_RISK_PER_TRADE_PCT
        )
        risk = abs(sl_price - entry_price)
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

        # Persist to database/paper_trades.db
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
        return True

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
            return self.execute_squareoff(symbol, exit_price=curr_sl, reason=result)

        # 2. Target Trigger
        if low <= tp:
            return self.execute_squareoff(symbol, exit_price=tp, reason=TradeExitReason.TARGET_HIT)

        # 3. Trail to Breakeven at +1R
        if not pos['trailed'] and low <= (entry_p - risk):
            pos['sl_price'] = entry_p
            pos['trailed'] = True
            update_trailing_sl(symbol, new_sl_price=entry_p, mode="paper")
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🛡️ [PAPER TRAIL] {symbol} reached +1R profit! SL moved to Breakeven (₹{entry_p:.2f}).")
            notify_trailing_sl(symbol=symbol, be_price=entry_p, mode="paper", config=self.config)

        # 4. Mandatory Squareoff
        if self.is_squareoff_time(now=now):
            return self.execute_squareoff(symbol, exit_price=current_ltp, reason=TradeExitReason.ALGO_SQUAREOFF_DAY_END)

        return None

    def execute_squareoff(self, symbol: str, exit_price: float, reason: str) -> Optional[Dict[str, Any]]:
        """Closes virtual position, computes fees, logs result, and archives to SQLite database."""
        if symbol not in self.active_positions:
            return None

        pos = self.active_positions.pop(symbol)
        entry_p = pos['entry_price']
        qty = pos['qty']
        
        gross_pnl = (entry_p - exit_price) * qty
        sell_turnover = entry_p * qty
        buy_turnover = exit_price * qty
        charges = calculate_charges(sell_turnover=sell_turnover, buy_turnover=buy_turnover)
        net_pnl = gross_pnl - charges
        pnl_pct = (entry_p - exit_price) / entry_p * 100
        actual_exit_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        close_and_archive_position(
            symbol=symbol,
            exit_price=exit_price,
            exit_time=actual_exit_time,
            result=reason,
            gross_pnl=gross_pnl,
            taxes_fees=charges,
            net_pnl=net_pnl,
            mode="paper"
        )

        display_result = EXIT_DISPLAY_LABELS.get(reason, reason)
        trade_record = {
            'symbol': symbol,
            'entry_time': pos.get('entry_time'),
            'exit_time': actual_exit_time,
            'entry_price': entry_p,
            'exit_price': exit_price,
            'qty': qty,
            'gross_pnl': gross_pnl,
            'taxes_fees': charges,
            'net_pnl': net_pnl,
            'pnl_pct': pnl_pct,
            'result': reason
        }
        self.closed_trades.append(trade_record)
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🏁 [PAPER EXIT] {symbol} @ ₹{exit_price:.2f} | Net PnL: ₹{net_pnl:+.2f} ({pnl_pct:+.2f}%) | {display_result}")
        notify_trade_exit(symbol=symbol, price=exit_price, net_pnl=net_pnl, pnl_pct=pnl_pct, reason=display_result, mode="paper", config=self.config)
        return trade_record


    def squareoff_all_positions(self) -> None:
        if not self.active_positions:
            return

        for symbol in list(self.active_positions.keys()):
            ticker = f"{symbol.replace('-EQ', '')}.NS" if not symbol.endswith('.NS') else symbol
            ltp = self.active_positions[symbol]['entry_price']
            try:
                # Use module-level fetch_latest_tick_price so unit test mocks attach cleanly
                tick = fetch_latest_tick_price(ticker) or fetch_latest_tick_price(symbol.replace('-EQ', ''))
                if tick is not None and tick.get('ltp'):
                    ltp = tick['ltp']
            except Exception:
                pass
            self.execute_squareoff(symbol=symbol, exit_price=ltp, reason=TradeExitReason.ALGO_SQUAREOFF_DAY_END)


if __name__ == "__main__":
    with prevent_sleep_context():
        engine = PaperTradingEngine()
        engine.run_live_loop()
