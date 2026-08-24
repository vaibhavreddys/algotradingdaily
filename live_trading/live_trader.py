"""
Real-Money Live Execution Engine with Shoonya OMS Integration.

Executes real-time orders on the National Stock Exchange (NSE) via Finvasia Shoonya:
  - Validates active session & capital limits before placing orders
  - Places MIS Short orders with linked Stop-Loss Limit protection orders (or 3-in-1 BO)
  - Modifies trigger price to Breakeven when +1R profit is achieved
  - Cancels open triggers and squares off at 15:00 IST
  - Persists state to database/live_trades.db and emits real-time Telegram alerts
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
from core.trade_db import (
    save_active_position,
    update_trailing_sl,
    close_and_archive_position,
    TradeExitReason,
    EXIT_DISPLAY_LABELS,
)
from alerts import notify_trade_entry, notify_trailing_sl, notify_trade_exit
from strategies.vwap_stoch_breakdown import STRATEGY_NAME, STRATEGY_VERSION


class LiveTradingEngine(BaseTradingEngine):
    """
    Live real-money order management system (OMS) connected directly to Shoonya API.
    Shares the exact same macro/micro scheduling loop and signal evaluation with PaperTradingEngine.
    """
    def __init__(self, config: TradingConfig = CONFIG):
        super().__init__(config=config, mode="live")

    def _place_bracket_order(
        self, symbol: str, qty: int, current_price: float, 
        risk: float, target_pts: float, sl_price: float, tp_price: float
    ) -> bool:
        """Executes a 3-in-1 Shoonya Bracket Order (product_type='B')."""
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚀 [REAL OMS] Placing BRACKET ORDER (BO): {qty}x {symbol} @ ₹{current_price:.2f} | SL pts: {risk:.2f} | TP pts: {target_pts:.2f}")
        try:
            order_res = self.place_order(
                buy_or_sell='S', product_type='B',
                exchange='NSE', tradingsymbol=symbol,
                quantity=qty, discloseqty=0, price_type='LMT',
                price=round(current_price, 2),
                bookloss_price=round(risk, 2),
                bookprofit_price=round(target_pts, 2),
                retention='DAY', remarks='VWAP-Stoch BO'
            )
            if order_res and order_res.get('stat') == 'Ok':
                norenordno = order_res.get('norenordno')
                entry_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.active_positions[symbol] = {
                    'symbol': symbol,
                    'entry_price': current_price,
                    'entry_time': entry_time,
                    'qty': qty,
                    'order_type': 'BO',
                    'entry_order_id': norenordno,
                    'sl_order_id': None,
                    'sl_price': sl_price,
                    'tp_price': tp_price,
                    'risk': risk,
                    'trailed': False
                }
                save_active_position(
                    symbol=symbol,
                    quantity=qty,
                    entry_price=current_price,
                    initial_sl=sl_price,
                    current_sl=sl_price,
                    target_price=tp_price,
                    strategy_name=STRATEGY_NAME,
                    strategy_version=STRATEGY_VERSION,
                    order_type='BO',
                    entry_order_id=norenordno,
                    sl_order_id=None,
                    mode='live'
                )
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ BO Active: {symbol} (Order ID: {norenordno})")
                notify_trade_entry(symbol=symbol, price=current_price, sl=sl_price, tp=tp_price, qty=qty, mode="live", config=self.config)
                return True
            print(f"⚠️ BO placement rejected for {symbol}: {order_res}")
            return False
        except Exception as e:
            print(f"❌ BO Exception for {symbol}: {e}")
            return False

    def _place_mis_order_with_sl(
        self, symbol: str, qty: int, current_price: float, 
        risk: float, sl_price: float, tp_price: float
    ) -> bool:
        """Executes Market Short MIS order and immediate Stop-Loss Limit protection order."""
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚀 [REAL OMS] Placing SHORT MIS: {qty}x {symbol} @ ₹{current_price:.2f} (SL: ₹{sl_price:.2f})")
        try:
            order_res = self.place_order(
                buy_or_sell='S', product_type='I',
                exchange='NSE', tradingsymbol=symbol,
                quantity=qty, discloseqty=0, price_type='MKT',
                price=0, retention='DAY', remarks='VWAP-Stoch Entry'
            )
            if not order_res or order_res.get('stat') != 'Ok':
                print(f"❌ MIS Entry rejected for {symbol}: {order_res}")
                return False

            norenordno = order_res.get('norenordno')
            entry_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # Immediate Stop-Loss Limit Protection Order (Buy SL-LMT)
            sl_limit_price = sl_price * 1.002
            sl_res = self.place_order(
                buy_or_sell='B', product_type='I',
                exchange='NSE', tradingsymbol=symbol,
                quantity=qty, discloseqty=0, price_type='SL-LMT',
                price=round(sl_limit_price, 2),
                trigger_price=round(sl_price, 2),
                retention='DAY', remarks='SL Protection'
            )
            sl_order_id = sl_res.get('norenordno') if sl_res else None

            self.active_positions[symbol] = {
                'symbol': symbol,
                'entry_price': current_price,
                'entry_time': entry_time,
                'qty': qty,
                'order_type': 'MIS',
                'entry_order_id': norenordno,
                'sl_order_id': sl_order_id,
                'sl_price': sl_price,
                'tp_price': tp_price,
                'risk': risk,
                'trailed': False
            }
            save_active_position(
                symbol=symbol,
                quantity=qty,
                entry_price=current_price,
                initial_sl=sl_price,
                current_sl=sl_price,
                target_price=tp_price,
                strategy_name=STRATEGY_NAME,
                strategy_version=STRATEGY_VERSION,
                order_type='MIS',
                entry_order_id=norenordno,
                sl_order_id=sl_order_id,
                mode='live'
            )
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ MIS Active: {symbol} (Linked SL ID: {sl_order_id})")
            notify_trade_entry(symbol=symbol, price=current_price, sl=sl_price, tp=tp_price, qty=qty, mode="live", config=self.config)
            return True

        except Exception as e:
            print(f"❌ MIS Exception for {symbol}: {e}")
            return False

    def execute_entry(self, symbol: str, entry_price: float, sl_price: float, tp_price: float) -> bool:
        """Executes Live Short Order using BO default or MIS with linked SL protection."""
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
        target_pts = abs(entry_price - tp_price)

        if getattr(self.config, 'ORDER_TYPE', 'BO') == "BO":
            success = self._place_bracket_order(symbol, qty, entry_price, risk, target_pts, sl_price, tp_price)
            if success:
                return True
            print(f"⚠️ BO failed for {symbol}. Attempting fallback to MIS...")

        return self._place_mis_order_with_sl(symbol, qty, entry_price, risk, sl_price, tp_price)

    def update_position(self, symbol: str, current_ltp: float, high: float, low: float, now: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
        """Micro guardian check for live positions (+1R trailing SL to Breakeven & TP/SL sync)."""
        if symbol not in self.active_positions:
            return None

        pos = self.active_positions[symbol]
        entry_p = pos['entry_price']
        risk = pos['risk']

        # Trail SL to Breakeven when price achieves +1R profit
        if not pos['trailed'] and low <= (entry_p - risk):
            self.execute_trailing_sl(symbol, be_price=entry_p)

        # Mandatory 3:00 PM Squareoff
        if self.is_squareoff_time(now=now):
            self.execute_squareoff(symbol, exit_price=current_ltp, reason=TradeExitReason.ALGO_SQUAREOFF_DAY_END)

        return None

    def execute_trailing_sl(self, symbol: str, be_price: float) -> bool:
        """Modifies existing SL trigger order to breakeven price at broker."""
        pos = self.active_positions.get(symbol)
        if not pos or not pos.get('sl_order_id'):
            return False

        try:
            be_limit_price = be_price * 1.002
            mod_res = self.modify_order(
                orderno=pos['sl_order_id'],
                exchange='NSE', tradingsymbol=symbol,
                newquantity=pos['qty'], newprice_type='SL-LMT',
                newprice=round(be_limit_price, 2),
                newtrigger_price=round(be_price, 2)
            )

            if mod_res and mod_res.get('stat') == 'Ok':
                pos['sl_price'] = be_price
                pos['trailed'] = True
                update_trailing_sl(symbol, new_sl_price=be_price, mode="live")
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🛡️ [REAL OMS] {symbol} SL modified to Breakeven (₹{be_price:.2f}).")
                notify_trailing_sl(symbol=symbol, be_price=be_price, mode="live", config=self.config)
                return True
            print(f"⚠️ Failed to modify SL for {symbol}: {mod_res}")
            return False
        except Exception as e:
            print(f"❌ Exception while modifying SL for {symbol}: {e}")
            return False

    def execute_squareoff(self, symbol: str, exit_price: float, reason: str) -> bool:
        """Cancels open SL orders, sends market square-off order, and archives to database/live_trades.db."""
        pos = self.active_positions.get(symbol)
        if not pos:
            return False

        try:
            # 1. Cancel open SL order
            if pos.get('sl_order_id'):
                self.cancel_order(orderno=pos['sl_order_id'])

            # 2. Square off with Market Buy
            res = self.place_order(
                buy_or_sell='B', product_type='I',
                exchange='NSE', tradingsymbol=symbol,
                quantity=pos['qty'], discloseqty=0, price_type='MKT',
                price=0, trigger_price=None, retention='DAY',
                remarks=f'Squareoff: {reason}'
            )

            pos_closed = self.active_positions.pop(symbol)
            entry_p = pos_closed['entry_price']
            qty = pos_closed['qty']
            
            from core.charges import calculate_charges
            gross_pnl = (entry_p - exit_price) * qty
            charges = calculate_charges(sell_turnover=entry_p * qty, buy_turnover=exit_price * qty)
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
                mode="live"
            )

            display_result = EXIT_DISPLAY_LABELS.get(reason, reason)
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🏁 [REAL OMS] {symbol} squared off ({reason}). Result: {res.get('stat') if res else 'None'}")
            notify_trade_exit(symbol=symbol, price=exit_price, net_pnl=net_pnl, pnl_pct=pnl_pct, reason=display_result, mode="live", config=self.config)
            return True
        except Exception as e:
            print(f"❌ Exception squaring off {symbol}: {e}")
            return False


if __name__ == "__main__":
    with prevent_sleep_context():
        engine = LiveTradingEngine()
        engine.run_live_loop()
