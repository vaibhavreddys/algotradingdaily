import unittest
import datetime
from config import TradingConfig
from live_trading.base_engine import BaseTradingEngine


class TestTimingScheduler(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(
            ENTRY_START_HOUR=10,
            ENTRY_START_MINUTE=0
        )
        self.engine = BaseTradingEngine(config=self.config)

    def test_get_seconds_until_market_open(self):
        # 09:00:00 AM -> 15 min 2 sec = 902 seconds
        test_now = datetime.datetime(2026, 8, 21, 9, 0, 0)
        secs = self.engine.get_seconds_until_market_open(test_now)
        self.assertEqual(secs, 902)

    def test_get_seconds_until_entry_window(self):
        # 09:15:00 AM -> 45 min 3 sec = 2703 seconds
        test_now = datetime.datetime(2026, 8, 21, 9, 15, 0)
        secs = self.engine.get_seconds_until_entry_window(test_now)
        self.assertEqual(secs, 2703)

    def test_get_next_market_session_same_day_premarket(self):
        # Friday 08:30 AM -> Today Friday 09:15 AM
        test_now = datetime.datetime(2026, 8, 21, 8, 30, 0) # Friday
        next_sess, remaining = self.engine.get_next_market_session(test_now)
        self.assertEqual(next_sess.weekday(), 4) # Friday
        self.assertEqual(next_sess.hour, 9)
        self.assertEqual(next_sess.minute, 15)
        self.assertEqual(remaining, 2700)

    def test_get_next_market_session_friday_after_hours(self):
        # Friday 16:00 PM -> Monday 09:15 AM
        test_now = datetime.datetime(2026, 8, 21, 16, 0, 0) # Friday
        next_sess, remaining = self.engine.get_next_market_session(test_now)
        self.assertEqual(next_sess.weekday(), 0) # Monday
        self.assertEqual(next_sess.day, 24)
        self.assertEqual(next_sess.hour, 9)
        self.assertEqual(next_sess.minute, 15)

    def test_get_next_market_session_saturday(self):
        # Saturday 12:00 PM -> Monday 09:15 AM
        test_now = datetime.datetime(2026, 8, 22, 12, 0, 0) # Saturday
        next_sess, remaining = self.engine.get_next_market_session(test_now)
        self.assertEqual(next_sess.weekday(), 0) # Monday
        self.assertEqual(next_sess.day, 24)


if __name__ == '__main__':
    unittest.main()
