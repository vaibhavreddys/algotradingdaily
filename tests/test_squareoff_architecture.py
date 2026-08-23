import unittest
import os
import sys
import datetime

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from strategies.vwap_stoch_breakdown import STRATEGY_INSTANCE
from core.market_calendar import get_platform_hard_squareoff_time


class TestTwoTierSquareoff(unittest.TestCase):
    def test_nse_squareoff(self):
        # NSE Closes at 15:30
        # Strategy (30m offset) -> 15:00
        # Platform (15m offset) -> 15:15
        # Effective = min(15:00, 15:15) = 15:00
        self.assertEqual(STRATEGY_INSTANCE.get_strategy_squareoff_time("NSE"), datetime.time(15, 0))
        self.assertEqual(get_platform_hard_squareoff_time("NSE"), datetime.time(15, 15))
        self.assertEqual(STRATEGY_INSTANCE.get_effective_squareoff_time("NSE"), datetime.time(15, 0))

    def test_mcx_squareoff(self):
        # MCX Closes at 23:30
        # Strategy (30m offset) -> 23:00
        # Platform (15m offset) -> 23:15
        # Effective = min(23:00, 23:15) = 23:00
        self.assertEqual(STRATEGY_INSTANCE.get_strategy_squareoff_time("MCX"), datetime.time(23, 0))
        self.assertEqual(get_platform_hard_squareoff_time("MCX"), datetime.time(23, 15))
        self.assertEqual(STRATEGY_INSTANCE.get_effective_squareoff_time("MCX"), datetime.time(23, 0))

    def test_us_equity_squareoff(self):
        # US Equity Closes at 16:00
        # Strategy (30m offset) -> 15:30
        # Platform (15m offset) -> 15:45
        # Effective = min(15:30, 15:45) = 15:30
        self.assertEqual(STRATEGY_INSTANCE.get_strategy_squareoff_time("US_EQUITY"), datetime.time(15, 30))
        self.assertEqual(get_platform_hard_squareoff_time("US_EQUITY"), datetime.time(15, 45))
        self.assertEqual(STRATEGY_INSTANCE.get_effective_squareoff_time("US_EQUITY"), datetime.time(15, 30))

    def test_crypto_squareoff(self):
        # Crypto is 24/7 continuous -> None
        self.assertIsNone(STRATEGY_INSTANCE.get_strategy_squareoff_time("CRYPTO"))
        self.assertIsNone(get_platform_hard_squareoff_time("CRYPTO"))
        self.assertIsNone(STRATEGY_INSTANCE.get_effective_squareoff_time("CRYPTO"))


if __name__ == "__main__":
    unittest.main()
