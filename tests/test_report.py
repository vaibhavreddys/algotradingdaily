import unittest
import io
import sys
import pandas as pd
from config import TradingConfig
from core.report import (
    format_outcome_distribution,
    print_simulation_report,
    print_multi_broker_matrix,
    print_daily_eod_report
)


class TestReportModule(unittest.TestCase):
    def test_format_outcome_distribution(self):
        counts = {"TARGET_HIT": 10, "SL_HIT": 20, "ALGO_SQUAREOFF_DAY_END": 30}
        out = format_outcome_distribution(counts, total_trades=60)
        self.assertIn("Outcome Distribution:", out)
        self.assertIn("TARGET HIT ✅", out)
        self.assertIn("SL HIT ❌", out)
        self.assertIn("3PM EXIT ⏱️", out)

    def test_print_simulation_report_captured(self):
        df = pd.DataFrame({
            'PnL %': [2.0, -1.0, 1.5],
            'Gross PnL (₹)': [200.0, -100.0, 150.0],
            'Net PnL (₹)': [180.0, -120.0, 130.0],
            'Capital': [10180.0, 10060.0, 10190.0],
            'Result': ['TARGET_HIT', 'SL_HIT', '3PM_EXIT'],
            'Entry Time': ['2026-06-01 10:15:00', '2026-06-02 10:30:00', '2026-06-03 11:00:00'],
            'Exit Time': ['2026-06-01 15:00:00', '2026-06-02 11:30:00', '2026-06-03 15:00:00']
        })
        cfg = TradingConfig(INITIAL_CAPITAL=10000.0, MAX_CONCURRENT_POSITIONS=2)

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            print_simulation_report(
                tdf=df,
                ending_capital=10190.0,
                total_charges=60.0,
                config=cfg,
                dataset_date_range=("2026-06-01", "2026-08-21", 59)
            )
        finally:
            sys.stdout = old_stdout

        out = captured.getvalue()
        self.assertIn("STRATEGY: VWAP-STOCH BREAKDOWN", out)
        self.assertIn("₹10,000 CAPITAL SIMULATION", out)
        self.assertIn("Ending Capital Balance : ₹10,190.00", out)
        self.assertIn("Net Return             : 1.90%", out)
        self.assertIn("Profit Factor", out)
        self.assertIn("Max Drawdown (MDD)", out)

    def test_print_daily_eod_report(self):
        day_trades = [{
            'symbol': 'TCS-EQ',
            'entry_price': 3500.0,
            'exit_price': 3450.0,
            'result': 'TARGET_HIT',
            'gross_pnl': 200.0,
            'taxes_fees': 20.0,
            'net_pnl': 180.0
        }]
        
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            msg, lines = print_daily_eod_report(
                day_trades=day_trades,
                initial_capital=10000.0,
                ending_balance=10180.0,
                date_str="2026-08-21"
            )
        finally:
            sys.stdout = old_stdout

        out = captured.getvalue()
        self.assertIn("DAILY EOD PERFORMANCE REPORT", out)
        self.assertIn("TCS-EQ", out)
        self.assertIn("Daily Paper Trading Summary", msg)
        self.assertEqual(len(lines), 1)


if __name__ == '__main__':
    unittest.main()
