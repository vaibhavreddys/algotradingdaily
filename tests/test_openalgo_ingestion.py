import datetime as dt
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


HAS_INGESTION_DEPS = all(importlib.util.find_spec(name) for name in ("duckdb", "tenacity"))


@unittest.skipUnless(HAS_INGESTION_DEPS, "OpenAlgo ingestion dependencies are not installed")
class TestOpenAlgoIngestion(unittest.TestCase):
    def setUp(self):
        from data_pipeline.openalgo_ingestion import settings
        from data_pipeline.openalgo_ingestion.downloader import ThrottledIngestionEngine

        self.settings = settings
        self.engine_class = ThrottledIngestionEngine
        self.temp_dir = tempfile.TemporaryDirectory()
        storage = Path(self.temp_dir.name)
        self.settings_patch = patch.multiple(
            settings,
            STORAGE_DIR=storage,
            DB_PATH=storage / "backtest_data.duckdb",
            STATE_DB_PATH=storage / "download_state.sqlite",
            LOG_PATH=storage / "ingestion.log",
            OPENALGO_API_KEY="test-key",
            OPENALGO_HOST="http://openalgo.test",
            SHOONYA_APPEND_EQ=False,
            DELAY_SECONDS=0,
        )
        self.settings_patch.start()

    def tearDown(self):
        self.settings_patch.stop()
        self.temp_dir.cleanup()

    def _engine(self, response):
        class Client:
            def history(self, **kwargs):
                self.kwargs = kwargs
                return response

        self.client = Client()
        return self.engine_class(client=self.client)

    def test_chunk_generation_and_invalid_dates(self):
        chunks = self.engine_class.generate_chunks(31, today=dt.date(2026, 8, 21))
        self.assertEqual(chunks, [("2026-07-22", "2026-08-20"), ("2026-08-21", "2026-08-21")])
        with self.assertRaisesRegex(ValueError, "start-date"):
            self.engine_class.generate_date_range_chunks("2026-08-21", "2026-08-20")

    def test_formats_eq_suffix_only_when_enabled(self):
        with patch.object(self.settings, "SHOONYA_APPEND_EQ", False):
            self.assertEqual(self.engine_class._format_symbol("reliance"), "RELIANCE")
        with patch.object(self.settings, "SHOONYA_APPEND_EQ", True):
            self.assertEqual(self.engine_class._format_symbol("reliance"), "RELIANCE-EQ")
            self.assertEqual(self.engine_class._format_symbol("RELIANCE-EQ"), "RELIANCE-EQ")

    def test_success_is_idempotent_and_reader_uses_ist_index(self):
        response = pd.DataFrame(
            {"Open": [100.0], "High": [102.0], "Low": [99.0], "Close": [101.0], "Volume": [50]},
            index=pd.DatetimeIndex(["2026-08-20 09:15:00+00:00"], name="timestamp"),
        )
        engine = self._engine(response)
        engine.ingest_date_range(["RELIANCE"], "2026-08-20", "2026-08-20")
        engine.ingest_date_range(["RELIANCE"], "2026-08-20", "2026-08-20")

        from data_pipeline.openalgo_ingestion import BacktestDataReader
        frame = BacktestDataReader().get_full_dataframe(symbol="RELIANCE")
        self.assertEqual(len(frame), 1)
        self.assertEqual(str(frame.index.tz), "Asia/Kolkata")
        self.assertEqual(frame.iloc[0]["close"], 101.0)
        self.assertTrue(engine.is_chunk_completed("RELIANCE", "2026-08-20", "2026-08-20"))

    def test_negative_and_null_volumes_are_clipped_to_zero(self):
        response = pd.DataFrame(
            {"Open": [100.0, 101.0], "High": [102.0, 103.0], "Low": [99.0, 100.0],
             "Close": [101.0, 102.0], "Volume": [-50, float("nan")]},
            index=pd.DatetimeIndex(
                ["2026-08-20 09:15:00+00:00", "2026-08-20 09:16:00+00:00"], name="timestamp"
            ),
        )
        engine = self._engine(response)
        engine.ingest_date_range(["RELIANCE"], "2026-08-20", "2026-08-20")

        from data_pipeline.openalgo_ingestion import BacktestDataReader
        frame = BacktestDataReader().get_full_dataframe(symbol="RELIANCE")
        self.assertEqual(frame["volume"].tolist(), [0, 0])

    def test_no_data_marks_completed_and_broker_error_marks_failed(self):
        no_data_engine = self._engine({"error_type": "no_data", "message": "holiday"})
        no_data_engine.ingest_date_range(["RELIANCE"], "2026-08-20", "2026-08-20")
        self.assertTrue(no_data_engine.is_chunk_completed("RELIANCE", "2026-08-20", "2026-08-20"))

        error_engine = self._engine({"error_type": "invalid_symbol", "message": "bad symbol"})
        with patch.object(error_engine, "_fetch_from_broker", side_effect=RuntimeError("invalid_symbol: bad symbol")):
            error_engine.ingest_date_range(["BROKEN"], "2026-08-20", "2026-08-20")
        self.assertFalse(error_engine.is_chunk_completed("BROKEN", "2026-08-20", "2026-08-20"))
