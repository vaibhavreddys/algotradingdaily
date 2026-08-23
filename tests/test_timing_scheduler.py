"""
Unit tests for Market Timing & Scheduler (core/market_calendar.py).
"""
import unittest
import datetime
from zoneinfo import ZoneInfo
from config import TradingConfig
from live_trading.base_engine import BaseTradingEngine


class TestTimingScheduler(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(EXCHANGE_MARKET="NSE")
        self.engine = BaseTradingEngine(config=self.config)

    def test_get_seconds_until_market_open(self):
        # 08:30:00 AM -> 45 min 2 sec = 2702 seconds
        tz = ZoneInfo("Asia/Kolkata")
        test_now = datetime.datetime(2026, 8, 21, 8, 30, 0, tzinfo=tz)
        seconds = self.engine.get_seconds_until_market_open(now=test_now)
        self.assertEqual(seconds, (45 * 60) + 2)

    def test_get_seconds_until_entry_window(self):
        # 09:15:00 AM -> 45 min 3 sec = 2703 seconds
        tz = ZoneInfo("Asia/Kolkata")
        test_now = datetime.datetime(2026, 8, 21, 9, 15, 0, tzinfo=tz)
        seconds = self.engine.get_seconds_until_entry_window(now=test_now)
        self.assertEqual(seconds, (45 * 60) + 3)

    def test_get_next_market_session_same_day_premarket(self):
        # Friday 08:30 AM -> Today Friday 09:15 AM
        tz = ZoneInfo("Asia/Kolkata")
        test_now = datetime.datetime(2026, 8, 21, 8, 30, 0, tzinfo=tz)
        next_session, rem_sec = self.engine.get_next_market_session(now=test_now)
        self.assertEqual(next_session.strftime("%Y-%m-%d %H:%M:%S"), "2026-08-21 09:15:00")
        self.assertEqual(rem_sec, (45 * 60))

    def test_get_next_market_session_friday_after_hours(self):
        # Friday 16:00 PM -> Monday 09:15 AM
        tz = ZoneInfo("Asia/Kolkata")
        test_now = datetime.datetime(2026, 8, 21, 16, 0, 0, tzinfo=tz)
        next_session, rem_sec = self.engine.get_next_market_session(now=test_now)
        self.assertEqual(next_session.strftime("%Y-%m-%d %H:%M:%S"), "2026-08-24 09:15:00")
        self.assertEqual(next_session.weekday(), 0)  # Monday

    def test_get_next_market_session_saturday(self):
        # Saturday 12:00 PM -> Monday 09:15 AM
        tz = ZoneInfo("Asia/Kolkata")
        test_now = datetime.datetime(2026, 8, 22, 12, 0, 0, tzinfo=tz)
        next_session, rem_sec = self.engine.get_next_market_session(now=test_now)
        self.assertEqual(next_session.strftime("%Y-%m-%d %H:%M:%S"), "2026-08-24 09:15:00")
        self.assertEqual(next_session.weekday(), 0)  # Monday


if __name__ == "__main__":
    unittest.main()
