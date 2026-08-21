"""
Real-Money Live Execution Engine with Shoonya OMS Integration.

Executes real-time orders on the National Stock Exchange (NSE) via Finvasia Shoonya:
  - Validates active session & capital limits before placing orders
  - Places MIS Short orders with linked Stop-Loss Limit protection orders
  - Modifies trigger price to Breakeven when +1R profit is achieved
  - Cancels open triggers and squares off at 15:00 IST
"""

import os
import sys
import datetime
from typing import Dict, Any, Optional

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


class LiveTradingEngine(BaseTradingEngine):
    """
    Live real-money order management system (OMS) connected directly to Shoonya API.
    """
    def __init__(self, config: TradingConfig = CONFIG):
        super().__init__(config=config)

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
                self.active_positions[symbol] = {
                    'symbol': symbol,
                    'entry_price': current_price,
                    'qty': qty,
                    'order_type': 'BO',
                    'entry_order_id': norenordno,
                    'sl_order_id': None,
                    'sl_price': sl_price,
                    'tp_price': tp_price,
                    'risk': risk,
                    'trailed': False
                }
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ BO Active: {symbol} (Order ID: {norenordno})")
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
                price=0, trigger_price=None, retention='DAY',
                remarks='VWAP-Stoch MIS Entry'
            )

            if not order_res or order_res.get('stat') != 'Ok':
                print(f"❌ MIS Entry Order Failed for {symbol}: {order_res}")
                return False

            norenordno = order_res.get('norenordno')

            # Linked Stop Loss Buy Order (SL-LMT)
            sl_buffer_price = sl_price * (1.0 + self.config.MIN_SL_BUFFER_PCT)
            sl_res = self.place_order(
                buy_or_sell='B', product_type='I',
                exchange='NSE', tradingsymbol=symbol,
                quantity=qty, discloseqty=0, price_type='SL-LMT',
                price=round(sl_buffer_price, 2),
                trigger_price=round(sl_price, 2),
                retention='DAY', remarks='SL Protection'
            )
            sl_order_id = sl_res.get('norenordno') if sl_res else None

            self.active_positions[symbol] = {
                'symbol': symbol,
                'entry_price': current_price,
                'qty': qty,
                'order_type': 'MIS',
                'entry_order_id': norenordno,
                'sl_order_id': sl_order_id,
                'sl_price': sl_price,
                'tp_price': tp_price,
                'risk': risk,
                'trailed': False
            }
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ MIS Active: {symbol} (Linked SL ID: {sl_order_id})")
            return True

        except Exception as e:
            print(f"❌ MIS Exception for {symbol}: {e}")
            return False

    def enter_short_order(self, symbol: str, current_price: float, sl_price: float, tp_price: float) -> bool:
        """
        Executes Intraday Short Entry order using Bracket Order (BO default) or MIS with linked SL.
        """
        if self.is_daily_circuit_breaker_active():
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚨 [CIRCUIT BREAKER] 3% Max Daily Loss reached. Rejecting entry for {symbol}.")
            return False

        if len(self.active_positions) >= self.config.MAX_CONCURRENT_POSITIONS:
            print(f"⚠️ Max position slots ({self.config.MAX_CONCURRENT_POSITIONS}) full. Rejecting entry for {symbol}.")
            return False

        current_capital = self.get_account_capital()
        qty = calculate_order_quantity(
            entry_price=current_price,
            current_capital=current_capital,
            max_concurrent_positions=self.config.MAX_CONCURRENT_POSITIONS,
            leverage_mis=self.config.LEVERAGE_MIS,
            sl_price=sl_price,
            max_risk_pct=self.config.MAX_RISK_PER_TRADE_PCT
        )
        risk = sl_price - current_price
        target_pts = current_price - tp_price

        # 1. Try Bracket Order (BO) if configured as default
        if self.config.ORDER_TYPE == "BO":
            success = self._place_bracket_order(symbol, qty, current_price, risk, target_pts, sl_price, tp_price)
            if success:
                return True
            print(f"⚠️ BO failed for {symbol}. Attempting fallback to MIS...")

        # 2. Standard MIS Execution (or Fallback from failed BO)
        return self._place_mis_order_with_sl(symbol, qty, current_price, risk, sl_price, tp_price)

    def trail_stop_loss_to_breakeven(self, symbol: str) -> bool:
        """Modifies existing SL trigger order to breakeven price at broker."""
        pos = self.active_positions.get(symbol)
        if not pos or not pos.get('sl_order_id'):
            return False

        try:
            be_price = pos['entry_price']
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
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🛡️ [REAL OMS] {symbol} SL modified to Breakeven (₹{be_price:.2f}).")
                return True
            print(f"⚠️ Failed to modify SL for {symbol}: {mod_res}")
            return False
        except Exception as e:
            print(f"❌ Exception while modifying SL for {symbol}: {e}")
            return False

    def exit_market_position(self, symbol: str, reason: str = "Exit") -> bool:
        """Cancels open SL orders and sends market square-off order."""
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

            self.active_positions.pop(symbol, None)
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🏁 [REAL OMS] {symbol} squared off ({reason}). Result: {res.get('stat') if res else 'None'}")
            return True
        except Exception as e:
            print(f"❌ Exception squaring off {symbol}: {e}")
            return False


if __name__ == "__main__":
    print(f"\n=======================================================")
    print("       LIVE REAL-MONEY TRADING ENGINE (SHOONYA OMS)")
    print(f"       Capital: ₹{CONFIG.INITIAL_CAPITAL:,.0f} | Max Slots: {CONFIG.MAX_CONCURRENT_POSITIONS}")
    print("=======================================================\n")
    engine = LiveTradingEngine()
    engine.authenticate()
