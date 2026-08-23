"""
Automated Parity Tests for Strategy Stop-Loss, Risk, and Take-Profit calculations.
"""

import unittest
import os
import sys

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from strategies.vwap_stoch_breakdown import (
    STRATEGY_INSTANCE,
    calculate_stop_and_target,
    SWING_SL_BUFFER_PCT,
    MIN_SL_BUFFER_PCT,
    RISK_REWARD_RATIO
)


class TestStrategyParity(unittest.TestCase):
    """Verifies that Strategy calculates correct SL, Risk, and TP."""

    def test_stop_loss_and_target_parity(self):
        """Tests SL, Risk, and TP calculations across diverse market price scenarios."""
        test_scenarios = [
            {"entry_p": 285.00, "swing_high": 286.00, "name": "ONGC-EQ Live Case"},
            {"entry_p": 1650.00, "swing_high": 1665.00, "name": "INFY-EQ High Price"},
            {"entry_p": 1263.90, "swing_high": 1270.00, "name": "RELIANCE-EQ Mid Risk"},
            {"entry_p": 100.00, "swing_high": 100.05, "name": "Flat Doji Consolidation (Floor Test)"},
        ]

        for sc in test_scenarios:
            entry_p = sc["entry_p"]
            swing_high = sc["swing_high"]

            with self.subTest(scenario=sc["name"]):
                sl, tp, risk = calculate_stop_and_target(entry_p, swing_high)

                raw_sl = max(
                    swing_high * (1.0 + SWING_SL_BUFFER_PCT),
                    entry_p * (1.0 + MIN_SL_BUFFER_PCT)
                )
                raw_risk = raw_sl - entry_p
                raw_tp = entry_p - (RISK_REWARD_RATIO * raw_risk)
                expected_sl = round(raw_sl, 2)
                expected_risk = round(raw_risk, 2)
                expected_tp = round(raw_tp, 2)

                self.assertEqual(sl, expected_sl)
                self.assertEqual(risk, expected_risk)
                self.assertEqual(tp, expected_tp)

    def test_trailing_stop_calculation(self):
        # Entry = 100, Risk = 2, Initial SL = 102. +1R target is reached when low <= 98
        entry = 100.0
        current_sl = 102.0
        risk = 2.0
        self.assertIsNone(STRATEGY_INSTANCE.calculate_trailing_stop(entry, current_sl, 99.0, 100.5, 98.5, risk))
        self.assertEqual(STRATEGY_INSTANCE.calculate_trailing_stop(entry, current_sl, 98.0, 100.0, 98.0, risk), 100.0)


if __name__ == "__main__":
    unittest.main()
