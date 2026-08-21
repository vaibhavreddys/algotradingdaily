import unittest
from config import TradingConfig
from core.risk import (
    calculate_stop_and_target,
    is_daily_loss_limit_reached,
    should_trail_to_breakeven,
    calculate_risk_based_quantity,
)


class TestRiskManagement(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(
            MIN_SL_BUFFER_PCT=0.0020,     # 0.2%
            SWING_SL_BUFFER_PCT=0.0005,   # 0.05%
            RISK_REWARD_RATIO=2.0,        # 1:2 R:R
            MAX_DAILY_LOSS_PCT=0.04,      # 4% Max Daily Loss
            MAX_RISK_PER_TRADE_PCT=0.01   # 1% Max Risk Per Trade
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
        # 10,000 capital, 4% limit = ₹400 loss
        # -₹200 loss -> Not reached
        self.assertFalse(is_daily_loss_limit_reached(today_realized_pnl=-200.0, day_starting_capital=10000.0, max_loss_pct=0.04))
        # -₹400 loss -> Reached exactly
        self.assertTrue(is_daily_loss_limit_reached(today_realized_pnl=-400.0, day_starting_capital=10000.0, max_loss_pct=0.04))
        # -₹450 loss -> Reached (exceeded)
        self.assertTrue(is_daily_loss_limit_reached(today_realized_pnl=-450.0, day_starting_capital=10000.0, max_loss_pct=0.04))
        # +₹150 profit -> Not reached
        self.assertFalse(is_daily_loss_limit_reached(today_realized_pnl=150.0, day_starting_capital=10000.0, max_loss_pct=0.04))

    def test_is_daily_loss_limit_zero_capital(self):
        self.assertTrue(is_daily_loss_limit_reached(today_realized_pnl=0.0, day_starting_capital=0.0))

    def test_should_trail_to_breakeven(self):
        self.assertFalse(should_trail_to_breakeven(entry_price=1000.0, risk=10.0, low_price=995.0, already_trailed=False))
        self.assertTrue(should_trail_to_breakeven(entry_price=1000.0, risk=10.0, low_price=990.0, already_trailed=False))
        self.assertTrue(should_trail_to_breakeven(entry_price=1000.0, risk=10.0, low_price=985.0, already_trailed=False))
        self.assertFalse(should_trail_to_breakeven(entry_price=1000.0, risk=10.0, low_price=985.0, already_trailed=True))

    def test_calculate_risk_based_quantity_normal(self):
        # Entry @ 1000, SL @ 1010 (Risk = 10 pts). Capital = 10000. 1% Risk = ₹100.
        # Risk Qty = 100 / 10 = 10 shares. Max Exposure = 25000 (Margin Qty = 25).
        # Min(10, 25) = 10 shares.
        qty = calculate_risk_based_quantity(
            entry_price=1000.0,
            sl_price=1010.0,
            current_capital=10000.0,
            max_risk_pct=0.01,
            max_exposure=25000.0
        )
        self.assertEqual(qty, 10)

    def test_calculate_risk_based_quantity_wide_sl_clipping(self):
        # Entry @ 1000, Wide SL @ 1050 (Risk = 50 pts). Capital = 10000. 1% Risk = ₹100.
        # Risk Qty = 100 / 50 = 2 shares. Max Exposure = 25000 (Margin Qty = 25).
        # Min(2, 25) = 2 shares! (Downsizes risk to strictly ₹100 max loss!)
        qty = calculate_risk_based_quantity(
            entry_price=1000.0,
            sl_price=1050.0,
            current_capital=10000.0,
            max_risk_pct=0.01,
            max_exposure=25000.0
        )
        self.assertEqual(qty, 2)

    def test_calculate_risk_based_quantity_margin_cap_dominant(self):
        # Entry @ 1000, Super Tight SL @ 1001 (Risk = 1 pt). Capital = 10000. 1% Risk = ₹100.
        # Risk Qty = 100 / 1 = 100 shares. Max Exposure = 25000 (Margin Qty = 25 shares).
        # Min(100, 25) = 25 shares! (Margin cap protects against over-leveraging!)
        qty = calculate_risk_based_quantity(
            entry_price=1000.0,
            sl_price=1001.0,
            current_capital=10000.0,
            max_risk_pct=0.01,
            max_exposure=25000.0
        )
        self.assertEqual(qty, 25)


if __name__ == '__main__':
    unittest.main()
