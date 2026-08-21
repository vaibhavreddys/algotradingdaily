import unittest
import os
import tempfile
import sqlite3
from core.trade_db import (
    init_db,
    save_active_position,
    update_trailing_sl,
    close_and_archive_position,
    get_active_positions,
    get_trade_journal,
    reconcile_stale_positions,
    get_db_connection,
    TradeExitReason,
)
from config import CONFIG, TradingConfig


class TestTradeDatabase(unittest.TestCase):
    def setUp(self):
        self.mode = "paper"
        init_db(self.mode)

    def test_save_and_get_active_position(self):
        save_active_position(
            symbol="TEST_SYM.NS",
            entry_order_id="ORD123",
            sl_order_id="SL123",
            qty=10,
            entry_p=100.0,
            sl_p=105.0,
            tp_p=90.0,
            order_type="BO",
            mode=self.mode
        )
        active = get_active_positions(self.mode)
        test_pos = [p for p in active if p['symbol'] == "TEST_SYM.NS"]
        self.assertEqual(len(test_pos), 1)
        self.assertEqual(test_pos[0]['entry_price'], 100.0)
        self.assertEqual(test_pos[0]['quantity'], 10)

    def test_update_trailing_sl(self):
        save_active_position(
            symbol="TEST_TRAIL.NS",
            entry_order_id="ORD456",
            sl_order_id="SL456",
            qty=5,
            entry_p=200.0,
            sl_p=210.0,
            tp_p=180.0,
            mode=self.mode
        )
        success = update_trailing_sl("TEST_TRAIL.NS", new_sl_price=200.0, mode=self.mode)
        self.assertTrue(success)
        active = get_active_positions(self.mode)
        pos = [p for p in active if p['symbol'] == "TEST_TRAIL.NS"][0]
        self.assertEqual(pos['current_sl'], 200.0)
        self.assertEqual(pos['status'], 'TRAILING')

    def test_close_and_archive_position_with_balance(self):
        save_active_position(
            symbol="TEST_CLOSE.NS",
            entry_order_id="ORD789",
            sl_order_id="SL789",
            qty=20,
            entry_p=50.0,
            sl_p=52.0,
            tp_p=46.0,
            mode=self.mode
        )
        archived = close_and_archive_position(
            symbol="TEST_CLOSE.NS",
            exit_price=46.0,
            exit_time="2026-08-21 15:00:00",
            result=TradeExitReason.TARGET_HIT,
            gross_pnl=80.0,
            taxes_fees=5.0,
            net_pnl=75.0,
            mode=self.mode
        )
        self.assertTrue(archived)
        # Verify removed from active
        active = get_active_positions(self.mode)
        self.assertFalse(any(p['symbol'] == "TEST_CLOSE.NS" for p in active))
        # Verify in journal with balance_after_trade
        journal = get_trade_journal(self.mode, limit=10)
        closed = [t for t in journal if t['symbol'] == "TEST_CLOSE.NS"]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]['result'], TradeExitReason.TARGET_HIT)
        self.assertIsNotNone(closed[0].get('balance_after_trade'))

    def tearDown(self):
        # Cleanup test entries
        with get_db_connection(self.mode) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM active_positions WHERE symbol LIKE 'TEST_%'")
            cursor.execute("DELETE FROM trade_history WHERE symbol LIKE 'TEST_%'")
            conn.commit()


if __name__ == '__main__':
    unittest.main()
