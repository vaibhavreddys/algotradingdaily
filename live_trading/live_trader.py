"""
Real-Money Live Execution Engine with OpenAlgo Unified OMS Integration.

Executes real-time orders on exchange via OpenAlgo Gateway across 24+ Indian brokers:
  - Validates active session & capital limits before placing orders
  - Places Short MIS orders with linked Stop-Loss protection orders
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
    Live real-money order management system (OMS) connected directly to OpenAlgo Gateway.
    Shares the exact same macro/micro scheduling loop and signal evaluation with PaperTradingEngine.
    """
    def __init__(self, config: TradingConfig = CONFIG):
        super().__init__(config=config, mode="live")

    def _place_openalgo_order(
        self,
        symbol: str,
        action: str,
        price_type: str,
        product: str,
        quantity: int,
        price: float = 0.0,
        trigger_price: float = 0.0,
        remarks: str = ""
    ) -> Optional[str]:
        """Helper to route order to OpenAlgo OMS client."""
        if not self.api:
            return None

        clean_sym = symbol.replace('.NS', '').replace('-EQ', '')
        exchange = getattr(self.config, 'EXCHANGE_MARKET', 'NSE')

        try:
            place_fn = getattr(self.api, 'placeorder', None) or getattr(self.api, 'place_order', None)
            if place_fn:
                res = place_fn(
                    symbol=clean_sym,
                    action=action,
                    exchange=exchange,
                    price_type=price_type,
                    product=product,
                    quantity=str(quantity),
                    price=str(round(price, 2)) if price > 0 else "0",
                    trigger_price=str(round(trigger_price, 2)) if trigger_price > 0 else "0",
                    strategy=STRATEGY_NAME
                )
                if res and isinstance(res, dict) and res.get('status') == 'success':
                    order_id = res.get('orderid', res.get('order_id', res.get('data', {}).get('orderid')))
                    return str(order_id)
        except Exception as e:
            print(f"❌ OpenAlgo placeorder exception for {symbol}: {e}")
        return None

    def execute_entry(self, symbol: str, entry_price: float, sl_price: float, tp_price: float) -> bool:
        """Executes Live Short Order using OpenAlgo Unified OMS with linked SL protection."""
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

        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚀 [OPENALGO OMS] Placing SHORT MIS: {qty}x {symbol} @ ₹{entry_price:.2f} (SL: ₹{sl_price:.2f})")

        # 1. Place Market Short Entry
        order_id = self._place_openalgo_order(
            symbol=symbol,
            action="SELL",
            price_type="MARKET",
            product="MIS",
            quantity=qty,
            remarks=f"{STRATEGY_NAME} Entry"
        )
        if not order_id:
            print(f"❌ Entry order rejected by OpenAlgo for {symbol}")
            return False

        # 2. Place Linked SL Protection (Buy SL-LMT)
        sl_limit_price = sl_price * 1.002
        sl_order_id = self._place_openalgo_order(
            symbol=symbol,
            action="BUY",
            price_type="SL-LMT",
            product="MIS",
            quantity=qty,
            price=sl_limit_price,
            trigger_price=sl_price,
            remarks=f"{STRATEGY_NAME} SL Protection"
        )

        entry_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.active_positions[symbol] = {
            'symbol': symbol,
            'entry_price': entry_price,
            'entry_time': entry_time,
            'qty': qty,
            'order_type': 'MIS',
            'entry_order_id': order_id,
            'sl_order_id': sl_order_id,
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
            entry_order_id=order_id,
            sl_order_id=sl_order_id,
            mode='live'
        )
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ MIS Active: {symbol} (Order: {order_id} | Linked SL: {sl_order_id})")
        notify_trade_entry(symbol=symbol, price=entry_price, sl=sl_price, tp=tp_price, qty=qty, mode="live", config=self.config)
        return True



    def execute_trailing_sl(self, symbol: str, be_price: float) -> bool:
        """Modifies existing SL trigger order to breakeven price via OpenAlgo."""
        pos = self.active_positions.get(symbol)
        if not pos or not pos.get('sl_order_id') or not self.api:
            return False

        clean_sym = symbol.replace('.NS', '').replace('-EQ', '')
        exchange = getattr(self.config, 'EXCHANGE_MARKET', 'NSE')

        try:
            be_limit_price = be_price * 1.002
            mod_fn = getattr(self.api, 'modifyorder', None) or getattr(self.api, 'modify_order', None)
            if mod_fn:
                res = mod_fn(
                    order_id=pos['sl_order_id'],
                    symbol=clean_sym,
                    action="BUY",
                    exchange=exchange,
                    price_type="SL-LMT",
                    product="MIS",
                    quantity=str(pos['qty']),
                    price=str(round(be_limit_price, 2)),
                    trigger_price=str(round(be_price, 2))
                )

                if res and isinstance(res, dict) and res.get('status') == 'success':
                    pos['sl_price'] = be_price
                    pos['trailed'] = True
                    update_trailing_sl(symbol, new_sl_price=be_price, mode="live")
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🛡️ [OPENALGO OMS] {symbol} SL modified to Breakeven (₹{be_price:.2f}).")
                    notify_trailing_sl(symbol=symbol, be_price=be_price, mode="live", config=self.config)
                    return True
            return False
        except Exception as e:
            print(f"❌ Exception while modifying SL via OpenAlgo for {symbol}: {e}")
            return False

    def execute_squareoff(self, symbol: str, exit_price: float, reason: str) -> bool:
        """Cancels open SL orders, sends market square-off order via OpenAlgo, and archives to live_trades.db."""
        pos = self.active_positions.get(symbol)
        if not pos:
            return False

        try:
            # 1. Cancel open SL order via OpenAlgo
            if pos.get('sl_order_id') and self.api:
                cancel_fn = getattr(self.api, 'cancelorder', None) or getattr(self.api, 'cancel_order', None)
                if cancel_fn:
                    cancel_fn(order_id=pos['sl_order_id'], strategy=STRATEGY_NAME)

            # 2. Square off with Market Buy via OpenAlgo
            self._place_openalgo_order(
                symbol=symbol,
                action="BUY",
                price_type="MARKET",
                product="MIS",
                quantity=pos['qty'],
                remarks=f"Squareoff: {reason}"
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
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🏁 [OPENALGO OMS] {symbol} squared off ({reason}).")
            notify_trade_exit(symbol=symbol, price=exit_price, net_pnl=net_pnl, pnl_pct=pnl_pct, reason=display_result, mode="live", config=self.config)
            return True
        except Exception as e:
            print(f"❌ Exception squaring off via OpenAlgo for {symbol}: {e}")
            return False


if __name__ == "__main__":
    with prevent_sleep_context():
        engine = LiveTradingEngine()
        engine.run_live_loop()
