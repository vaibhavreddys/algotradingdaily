import unittest
from config import TradingConfig
from core.capital import (
    get_slot_margin,
    get_slot_exposure,
    calculate_order_quantity,
    get_persisted_paper_capital,
)


class TestCapitalManagement(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(
            INITIAL_CAPITAL=10000.0,
            MAX_CONCURRENT_POSITIONS=2,
            LEVERAGE_MIS=5
        )

    def test_get_slot_margin_equal_split(self):
        self.assertEqual(get_slot_margin(10000.0, 2), 5000.0)
        self.assertEqual(get_slot_margin(12000.0, 3), 4000.0)
        self.assertEqual(get_slot_margin(15000.0, 1), 15000.0)

    def test_get_slot_margin_invalid_slots(self):
        with self.assertRaises(ValueError):
            get_slot_margin(10000.0, 0)
        with self.assertRaises(ValueError):
            get_slot_margin(10000.0, -1)

    def test_get_slot_margin_zero_or_negative_capital(self):
        self.assertEqual(get_slot_margin(0.0, 2), 0.0)
        self.assertEqual(get_slot_margin(-500.0, 2), 0.0)

    def test_get_slot_exposure_mis_leverage(self):
        self.assertEqual(get_slot_exposure(10000.0, 2, 5), 25000.0)
        self.assertEqual(get_slot_exposure(15000.0, 3, 5), 25000.0)
        self.assertEqual(get_slot_exposure(12000.0, 2, 5), 30000.0)

    def test_calculate_order_quantity(self):
        self.assertEqual(calculate_order_quantity(2500.0, 10000.0, 2, 5), 10)
        self.assertEqual(calculate_order_quantity(1120.0, 10000.0, 2, 5), 22)
        self.assertEqual(calculate_order_quantity(30000.0, 10000.0, 2, 5), 0)

    def test_calculate_order_quantity_zero_inputs(self):
        self.assertEqual(calculate_order_quantity(0.0, 10000.0, 2, 5), 0)
        self.assertEqual(calculate_order_quantity(2500.0, 0.0, 2, 5), 0)

    def test_get_persisted_paper_capital_fallback(self):
        cap = get_persisted_paper_capital(initial_capital=10000.0)
        self.assertIsInstance(cap, float)
        self.assertGreater(cap, 0.0)


if __name__ == '__main__':
    unittest.main()
