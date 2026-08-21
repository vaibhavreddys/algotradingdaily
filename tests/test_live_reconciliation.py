import unittest
import os
import sqlite3
import datetime
from unittest.mock import MagicMock, patch
from config import TradingConfig
from live_trading.base_engine import BaseTradingEngine
from core.trade_db import (
    init_db,
    save_active_position,
    get_active_positions,
    get_trade_journal,
    reconcile_stale_positions,
    get_db_path,
    get_db_connection
)


class TestLiveReconciliation(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(TRADING_MODE="live", INITIAL_CAPITAL=10000.0)
        self.engine = BaseTradingEngine(config=self.config)
        self.db_path = get_db_path("live")
        init_db("live")
        self._clean_test_data()

    def tearDown(self):
        self._clean_test_data()

    def _clean_test_data(self):
        with get_db_connection("live") as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM active_positions WHERE symbol LIKE 'TEST_%'")
            cursor.execute("DELETE FROM trade_history WHERE symbol LIKE 'TEST_%'")
            conn.commit()

    def test_reconcile_specific_symbol(self):
        save_active_position(
            symbol="TEST_RECON_LIVE",
            entry_order_id="1111",
            sl_order_id="2222",
            qty=10,
            entry_p=1000.0,
            sl_p=1020.0,
            tp_p=960.0,
            order_type="MIS",
            mode="live"
        )

        active = get_active_positions(mode="live")
        self.assertTrue(any(p['symbol'] == "TEST_RECON_LIVE" for p in active))

        # Reconcile specific symbol
        reconciled = reconcile_stale_positions(mode="live", specific_symbol="TEST_RECON_LIVE")
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]['symbol'], "TEST_RECON_LIVE")

        # Active positions should be empty for that symbol
        active_after = get_active_positions(mode="live")
        self.assertFalse(any(p['symbol'] == "TEST_RECON_LIVE" for p in active_after))

        # Trade journal should have it archived
        journal = get_trade_journal(mode="live")
        self.assertTrue(any(p['symbol'] == "TEST_RECON_LIVE" for p in journal))

    def test_sync_active_positions_with_broker_zero_qty(self):
        save_active_position(
            symbol="TEST_BROKER_ZERO",
            entry_order_id="3333",
            sl_order_id="4444",
            qty=5,
            entry_p=500.0,
            sl_p=510.0,
            tp_p=480.0,
            order_type="MIS",
            mode="live"
        )

        # Mock broker get_positions returning 0 net quantity
        self.engine.get_positions = MagicMock(return_value=[
            {'tsym': 'TEST_BROKER_ZERO', 'netqty': '0'}
        ])

        # Run sync_active_positions_from_db
        count = self.engine.sync_active_positions_from_db(mode="live")

        # Should be reconciled and 0 active positions
        self.assertNotIn("TEST_BROKER_ZERO", self.engine.active_positions)
        journal = get_trade_journal(mode="live")
        self.assertTrue(any(p['symbol'] == "TEST_BROKER_ZERO" for p in journal))


if __name__ == '__main__':
    unittest.main()
