import unittest
import io
import sys
import pandas as pd
from config import TradingConfig
from live_trading.base_engine import BaseTradingEngine
from strategies.vwap_stoch_breakdown import evaluate_signals
from data_pipeline.data_feed import load_candle_data, fetch_nifty_benchmark


class TestFunnelTelemetry(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(
            INITIAL_CAPITAL=10000.0,
            MAX_CONCURRENT_POSITIONS=2,
            )
        self.engine = BaseTradingEngine(config=self.config)

    def test_render_filter_funnel_output(self):
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            self.engine.render_filter_funnel(
                eval_count=50,
                total_symbols=50,
                rel_weak_count=24,
                vwap_count=29,
                adx_count=20,
                stoch_count=1,
                signals_fired=1
            )
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        self.assertIn("15m Scan Funnel (50/50 constituents evaluated):", output)
        self.assertIn("Relative Weakness vs NIFTY : 24/50 stocks", output)
        self.assertIn("Price < Intraday VWAP       : 29/50 stocks", output)
        self.assertIn("Strong ADX Trend (ADX > 25) : 20/50 stocks", output)
        self.assertIn("Stochastic RSI Breakdown    :  1/50 stocks", output)
        self.assertIn("Qualified Entries Fired    :  1 trade(s) | Open Slots: 0/2", output)

    def test_sub_filter_boolean_flags_present(self):
        nifty = fetch_nifty_benchmark(self.config)
        df = load_candle_data('TCS.NS', self.config)
        res = evaluate_signals(df, nifty_pct_map=nifty, config=self.config)
        self.assertIsNotNone(res)
        self.assertIn('Rel_Weakness_Pass', res.columns)
        self.assertIn('VWAP_Pass', res.columns)
        self.assertIn('ADX_Pass', res.columns)
        self.assertIn('Stoch_Pass', res.columns)
        self.assertIn('Signal', res.columns)


if __name__ == '__main__':
    unittest.main()
