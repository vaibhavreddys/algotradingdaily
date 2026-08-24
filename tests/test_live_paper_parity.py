import unittest
import datetime
from config import CONFIG
from live_trading.base_engine import BaseTradingEngine
from live_trading.paper_trader import PaperTradingEngine
from live_trading.live_trader import LiveTradingEngine
from core.trade_db import get_db_path


class TestLivePaperParity(unittest.TestCase):

    def test_inheritance_and_hooks_exist(self):
        paper = PaperTradingEngine(config=CONFIG)
        live = LiveTradingEngine(config=CONFIG)

        # 1. Both inherit BaseTradingEngine
        self.assertIsInstance(paper, BaseTradingEngine)
        self.assertIsInstance(live, BaseTradingEngine)

        # 2. Both have respective modes set
        self.assertEqual(paper.mode, "paper")
        self.assertEqual(live.mode, "live")

        # 3. Both implement required abstract hooks
        for hook in ['execute_entry', 'update_position', 'execute_squareoff']:
            self.assertTrue(callable(getattr(paper, hook)))
            self.assertTrue(callable(getattr(live, hook)))

        # 4. Database paths are completely isolated
        paper_db = get_db_path("paper")
        live_db = get_db_path("live")
        self.assertNotEqual(paper_db, live_db)
        self.assertIn("paper_trades.db", paper_db)
        self.assertIn("live_trades.db", live_db)

    def test_shared_timing_logic(self):
        paper = PaperTradingEngine(config=CONFIG)
        live = LiveTradingEngine(config=CONFIG)

        # Test market open calculation parity
        test_dt = datetime.datetime(2026, 8, 24, 8, 45, 0)
        self.assertEqual(paper.get_seconds_until_market_open(test_dt), live.get_seconds_until_market_open(test_dt))

        # Test entry window parity
        self.assertFalse(paper.is_entry_window_active(test_dt))
        self.assertFalse(live.is_entry_window_active(test_dt))

        entry_dt = datetime.datetime(2026, 8, 24, 10, 15, 0)
        self.assertTrue(paper.is_entry_window_active(entry_dt))
        self.assertTrue(live.is_entry_window_active(entry_dt))

        # Test 15m candle wait time parity
        self.assertEqual(
            paper.get_seconds_until_next_candle(15, entry_dt),
            live.get_seconds_until_next_candle(15, entry_dt)
        )


if __name__ == '__main__':
    unittest.main()
