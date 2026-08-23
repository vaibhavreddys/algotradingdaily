"""
Unit tests for Platform-Level Risk Management (core/risk.py).
"""
import unittest
from config import TradingConfig
from core.risk import (
    is_daily_loss_limit_reached,
    calculate_risk_based_quantity,
    should_trail_to_breakeven,
)


class TestRiskManagement(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(
            MAX_DAILY_LOSS_PCT=0.04,      # 4% Max Daily Loss
            MAX_RISK_PER_TRADE_PCT=0.01   # 1% Max Risk Per Trade
        )

    def test_is_daily_loss_limit_reached(self):
        # Starting Capital = 10,000 | 4% Limit = -400
        cap = 10000.0
        self.assertFalse(is_daily_loss_limit_reached(-100.0, cap, self.config.MAX_DAILY_LOSS_PCT))
        self.assertFalse(is_daily_loss_limit_reached(-399.9, cap, self.config.MAX_DAILY_LOSS_PCT))
        self.assertTrue(is_daily_loss_limit_reached(-400.0, cap, self.config.MAX_DAILY_LOSS_PCT))
        self.assertTrue(is_daily_loss_limit_reached(-500.0, cap, self.config.MAX_DAILY_LOSS_PCT))

    def test_is_daily_loss_limit_zero_capital(self):
        self.assertTrue(is_daily_loss_limit_reached(0.0, 0.0))

    def test_calculate_risk_based_quantity_unconstrained(self):
        # Cap = 10,000 | Risk 1% = 100 | Entry = 500, SL = 510 (Per share risk = 10)
        # Qty = 100 / 10 = 10 shares
        qty = calculate_risk_based_quantity(
            entry_price=500.0,
            sl_price=510.0,
            current_capital=10000.0,
            max_risk_pct=0.01,
            max_exposure=None
        )
        self.assertEqual(qty, 10)

    def test_calculate_risk_based_quantity_margin_constrained(self):
        # Cap = 10,000 | Risk 1% = 100 | Entry = 500, SL = 502 (Risk Qty = 50)
        # Slot exposure = 10,000 (Margin Qty = 20) -> Min is 20
        qty = calculate_risk_based_quantity(
            entry_price=500.0,
            sl_price=502.0,
            current_capital=10000.0,
            max_risk_pct=0.01,
            max_exposure=10000.0
        )
        self.assertEqual(qty, 20)

    def test_should_trail_to_breakeven(self):
        # Short Trade: Entry = 100.0, SL = 102.0 (Risk = 2.0) -> +1R target = 98.0
        entry = 100.0
        sl = 102.0
        self.assertFalse(should_trail_to_breakeven(entry, current_ltp=99.0, initial_sl=sl, current_sl=sl))
        self.assertTrue(should_trail_to_breakeven(entry, current_ltp=98.0, initial_sl=sl, current_sl=sl))
        self.assertTrue(should_trail_to_breakeven(entry, current_ltp=97.5, initial_sl=sl, current_sl=sl))
        # Already trailed -> False
        self.assertFalse(should_trail_to_breakeven(entry, current_ltp=97.0, initial_sl=sl, current_sl=entry))


if __name__ == "__main__":
    unittest.main()
