import unittest
import datetime
from zoneinfo import ZoneInfo
from config import CONFIG, TradingConfig, EXCHANGE_PROFILES
from core.market_calendar import (
    get_market_profile,
    get_market_timezone,
    is_continuous_market,
    get_market_open_close,
    get_strategy_entry_window,
    get_squareoff_time,
    is_market_open,
    is_market_closed,
    is_entry_window_active,
    is_squareoff_time,
    get_seconds_until_market_open,
    get_seconds_until_entry_window,
    get_next_market_session,
)


class TestMarketCalendar(unittest.TestCase):
    def test_nse_profile_and_offsets(self):
        self.assertEqual(get_market_timezone("NSE"), ZoneInfo("Asia/Kolkata"))
        self.assertFalse(is_continuous_market("NSE"))
        self.assertEqual(get_market_open_close("NSE"), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertEqual(get_strategy_entry_window("NSE", warmup_minutes=45, cutoff_minutes=120), (datetime.time(10, 0), datetime.time(13, 30)))
        self.assertEqual(get_squareoff_time("NSE"), datetime.time(15, 0))

    def test_bse_profile_and_offsets(self):
        self.assertEqual(get_market_timezone("BSE"), ZoneInfo("Asia/Kolkata"))
        self.assertFalse(is_continuous_market("BSE"))
        self.assertEqual(get_market_open_close("BSE"), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertEqual(get_strategy_entry_window("BSE", warmup_minutes=45, cutoff_minutes=120), (datetime.time(10, 0), datetime.time(13, 30)))
        self.assertEqual(get_squareoff_time("BSE"), datetime.time(15, 0))

    def test_mcx_profile_and_offsets(self):
        self.assertEqual(get_market_open_close("MCX"), (datetime.time(9, 0), datetime.time(23, 30)))
        self.assertEqual(get_strategy_entry_window("MCX", warmup_minutes=45, cutoff_minutes=120), (datetime.time(9, 45), datetime.time(21, 30)))
        self.assertEqual(get_squareoff_time("MCX"), datetime.time(23, 15))

    def test_us_equity_profile_and_timezone(self):
        self.assertEqual(get_market_timezone("US_EQUITY"), ZoneInfo("America/New_York"))
        self.assertEqual(get_market_open_close("US_EQUITY"), (datetime.time(9, 30), datetime.time(16, 0)))
        self.assertEqual(get_strategy_entry_window("US_EQUITY", warmup_minutes=45, cutoff_minutes=120), (datetime.time(10, 15), datetime.time(14, 0)))
        self.assertEqual(get_squareoff_time("US_EQUITY"), datetime.time(15, 45))

    def test_crypto_continuous_market(self):
        self.assertTrue(is_continuous_market("CRYPTO"))
        self.assertEqual(get_strategy_entry_window("CRYPTO"), (None, None))
        self.assertIsNone(get_squareoff_time("CRYPTO"))
        self.assertTrue(is_market_open("CRYPTO"))
        self.assertFalse(is_market_closed("CRYPTO"))
        self.assertTrue(is_entry_window_active("CRYPTO"))
        self.assertFalse(is_squareoff_time("CRYPTO"))
        self.assertEqual(get_seconds_until_market_open("CRYPTO"), 0)

    def test_is_market_open_and_closed_on_weekday_vs_weekend(self):
        tz = ZoneInfo("Asia/Kolkata")
        # Tuesday 11:00 AM IST -> Open
        tue_open = datetime.datetime(2026, 8, 18, 11, 0, 0, tzinfo=tz)
        self.assertTrue(is_market_open("NSE", now=tue_open))
        self.assertFalse(is_market_closed("NSE", now=tue_open))

        # Tuesday 16:00 PM IST -> Closed
        tue_closed = datetime.datetime(2026, 8, 18, 16, 0, 0, tzinfo=tz)
        self.assertFalse(is_market_open("NSE", now=tue_closed))
        self.assertTrue(is_market_closed("NSE", now=tue_closed))

        # Saturday 11:00 AM IST -> Closed
        sat_time = datetime.datetime(2026, 8, 22, 11, 0, 0, tzinfo=tz)
        self.assertFalse(is_market_open("NSE", now=sat_time))
        self.assertTrue(is_market_closed("NSE", now=sat_time))

    def test_next_market_session_calculation(self):
        tz = ZoneInfo("Asia/Kolkata")
        # Friday 16:00 PM IST -> Next session Monday 09:15 AM IST
        fri_close = datetime.datetime(2026, 8, 21, 16, 0, 0, tzinfo=tz)
        next_dt, rem_sec = get_next_market_session("NSE", now=fri_close)
        self.assertEqual(next_dt.weekday(), 0)  # Monday
        self.assertEqual(next_dt.time(), datetime.time(9, 15))
        self.assertGreater(rem_sec, 0)


if __name__ == "__main__":
    unittest.main()
