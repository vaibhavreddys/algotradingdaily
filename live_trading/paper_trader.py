"""
Paper Trading Engine with Universal Parity Architecture.

Inherits identical 15m scanning, 15s position guardian, timing scheduler,
and risk budgeting from BaseTradingEngine:
  - Executes virtual fills with zero financial risk
  - Tracks trailing stop-loss to breakeven at +1R
  - Persists trade journal to database/paper_trades.db
  - Emits real-time simulated order alerts to Telegram
"""

import os
import sys
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
from strategies.vwap_stoch_breakdown import STRATEGY_NAME, STRATEGY_VERSION
from data_pipeline import fetch_latest_tick_price


class PaperTradingEngine(BaseTradingEngine):
    """
    Virtual Paper Trading Engine.
    Implements entry, trailing SL, and square-off hooks using virtual fills and paper_trades.db.
    """
    def __init__(self, config: TradingConfig = CONFIG):
        super().__init__(config=config, mode="paper")
        self.paper_trades = self.closed_trades

    def execute_entry(self, symbol: str, entry_price: float, sl_price: float, tp_price: float) -> bool:
        """Executes virtual paper entry with position sizing and database persistence."""
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

        save_active_position(
            symbol=symbol,
            quantity=qty,
            entry_price=entry_price,
            initial_sl=sl_price,
            current_sl=sl_price,
            target_price=tp_price,
            strategy_name=STRATEGY_NAME,
            strategy_version=STRATEGY_VERSION,
            order_type='MIS',
            entry_order_id='PAPER_ENTRY',
            sl_order_id='PAPER_SL',
            mode="paper"
        )

        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚀 [PAPER SHORT] {qty}x {symbol} @ ₹{entry_price:.2f} | SL: ₹{sl_price:.2f} | TP: ₹{tp_price:.2f}")
        notify_trade_entry(symbol=symbol, price=entry_price, sl=sl_price, tp=tp_price, qty=qty, mode="paper", config=self.config)
        return True

    def execute_trailing_sl(self, symbol: str, be_price: float) -> bool:
        """Moves virtual stop loss to breakeven in paper_trades.db."""
        pos = self.active_positions.get(symbol)
        if not pos:
            return False

        pos['sl_price'] = be_price
        pos['trailed'] = True
        update_trailing_sl(symbol, new_sl_price=be_price, mode="paper")
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🛡️ [PAPER TRAIL] {symbol} reached +1R profit! SL moved to Breakeven (₹{be_price:.2f}).")
        notify_trailing_sl(symbol=symbol, be_price=be_price, mode="paper", config=self.config)
        return True

    def execute_squareoff(self, symbol: str, exit_price: float, reason: str) -> Optional[Dict[str, Any]]:
        """Closes virtual position, computes fees, logs result, and archives to database/paper_trades.db."""
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


if __name__ == "__main__":
    with prevent_sleep_context():
        engine = PaperTradingEngine()
        engine.run_live_loop()
