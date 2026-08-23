import unittest
import os
import sys
import pandas as pd

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_pipeline import load_candle_data, get_available_symbols
from data_pipeline.openalgo_ingestion import settings


class TestDuckDBPipeline(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(settings.DB_PATH), "DuckDB file not present")
    def test_load_candle_data_duckdb_resolution(self):
        df = load_candle_data("RELIANCE.NS", interval="15m", verbose=False)
        self.assertIsNotNone(df)
        self.assertFalse(df.empty)
        self.assertGreaterEqual(len(df), 500)
        
        # Verify standardized column names
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            self.assertIn(col, df.columns)
            
        # Verify timezone awareness
        self.assertIsNotNone(df.index.tz)

    @unittest.skipUnless(os.path.exists(settings.DB_PATH), "DuckDB file not present")
    def test_get_available_symbols_duckdb(self):
        symbols = get_available_symbols(universe="ALL")
        self.assertGreaterEqual(len(symbols), 100)
        self.assertTrue(any("RELIANCE" in s for s in symbols))


if __name__ == "__main__":
    unittest.main()
