import unittest
from config import TradingConfig
from core.risk import (
    calculate_stop_and_target,
    is_daily_loss_limit_reached,
    should_trail_to_breakeven,
)


class TestRiskManagement(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(
            MIN_SL_BUFFER_PCT=0.0020,     # 0.2%
            SWING_SL_BUFFER_PCT=0.0005,   # 0.05%
            RISK_REWARD_RATIO=2.0,        # 1:2 R:R
            MAX_DAILY_LOSS_PCT=0.03       # 3% Max Daily Loss
        )

    def test_calculate_stop_and_target_swing_dominant(self):
        sl, tp, risk = calculate_stop_and_target(entry_price=1000.0, swing_high=1010.0, config=self.config)
        self.assertEqual(sl, 1010.5)
        self.assertEqual(tp, 978.99)
        self.assertEqual(risk, 10.5)

    def test_calculate_stop_and_target_min_buffer_dominant(self):
        sl, tp, risk = calculate_stop_and_target(entry_price=1000.0, swing_high=1000.50, config=self.config)
        self.assertEqual(sl, 1002.0)
        self.assertEqual(tp, 996.0)
        self.assertEqual(risk, 2.0)

    def test_calculate_stop_and_target_zero_entry(self):
        sl, tp, risk = calculate_stop_and_target(entry_price=0.0, swing_high=100.0, config=self.config)
        self.assertEqual(sl, 0.0)
        self.assertEqual(tp, 0.0)
        self.assertEqual(risk, 0.0)

    def test_is_daily_loss_limit_reached(self):
        # 10,000 capital, 3% limit = ₹300 loss
        # -₹200 loss -> Not reached
        self.assertFalse(is_daily_loss_limit_reached(today_realized_pnl=-200.0, day_starting_capital=10000.0, max_loss_pct=0.03))
        # -₹300 loss -> Reached exactly
        self.assertTrue(is_daily_loss_limit_reached(today_realized_pnl=-300.0, day_starting_capital=10000.0, max_loss_pct=0.03))
        # -₹450 loss -> Reached (exceeded)
        self.assertTrue(is_daily_loss_limit_reached(today_realized_pnl=-450.0, day_starting_capital=10000.0, max_loss_pct=0.03))
        # +₹150 profit -> Not reached
        self.assertFalse(is_daily_loss_limit_reached(today_realized_pnl=150.0, day_starting_capital=10000.0, max_loss_pct=0.03))

    def test_is_daily_loss_limit_zero_capital(self):
        self.assertTrue(is_daily_loss_limit_reached(today_realized_pnl=0.0, day_starting_capital=0.0))

    def test_should_trail_to_breakeven(self):
        # Short Entry @ 1000, risk = 10 -> +1R level is 990
        # low = 995 -> Not reached
        self.assertFalse(should_trail_to_breakeven(entry_price=1000.0, risk=10.0, low_price=995.0, already_trailed=False))
        # low = 990 -> Reached +1R
        self.assertTrue(should_trail_to_breakeven(entry_price=1000.0, risk=10.0, low_price=990.0, already_trailed=False))
        # low = 985 -> Reached +1R
        self.assertTrue(should_trail_to_breakeven(entry_price=1000.0, risk=10.0, low_price=985.0, already_trailed=False))
        # already trailed -> False
        self.assertFalse(should_trail_to_breakeven(entry_price=1000.0, risk=10.0, low_price=985.0, already_trailed=True))


if __name__ == '__main__':
    unittest.main()
