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
            APPEND_EQ=False,
            DELAY_SECONDS=0,
            PROBE_DELAY_SECONDS=0,
        )
        self.settings_patch.start()

    def tearDown(self):
        self.settings_patch.stop()
        import gc
        gc.collect()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

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
        with patch.object(self.settings, "APPEND_EQ", False, create=True):
            self.assertEqual(self.engine_class._format_symbol("reliance"), "RELIANCE")
        with patch.object(self.settings, "APPEND_EQ", True, create=True):
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

    def test_history_boundary_detection_and_clamping(self):
        class BoundaryClient:
            def __init__(self):
                self.calls = []

            def history(self, **kwargs):
                self.calls.append((kwargs["start_date"], kwargs["end_date"]))
                if kwargs["start_date"] >= "2025-12-28":
                    return pd.DataFrame(
                        {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
                        index=pd.DatetimeIndex(["2026-01-02T09:15:00+00:00"], name="timestamp"),
                    )
                return {"error_type": "no_data", "message": "none"}

        self.client = BoundaryClient()
        engine = self.engine_class(client=self.client)
        boundary = engine.detect_history_start(force=True)
        self.assertEqual(boundary, "2025-12-28")

        calls_before_ingest = len(self.client.calls)
        engine.ingest_date_range(["TEST"], "2025-01-01", "2026-01-10")
        ingest_calls = self.client.calls[calls_before_ingest:]
        self.assertTrue(ingest_calls)
        self.assertEqual(ingest_calls[0][0], "2025-12-28")
        self.assertTrue(engine.is_chunk_completed("TEST", "2025-12-28", "2026-01-10"))

    def test_range_entirely_before_boundary_aborts_without_calls(self):
        class SilentClient:
            def __init__(self):
                self.calls = []

            def history(self, **kwargs):
                self.calls.append(kwargs)

        self.client = SilentClient()
        engine = self.engine_class(client=self.client)
        engine._meta_set(engine.HISTORY_START_KEY, "2026-06-01")
        engine.ingest_date_range(["TEST"], "2025-01-01", "2025-12-31")
        self.assertEqual(self.client.calls, [])
        self.assertFalse(engine.is_chunk_completed("TEST", "2025-01-01", "2025-01-30"))

    def test_dataset_card_contains_stats_and_recipes(self):
        import duckdb

        con = duckdb.connect(str(self.settings.DB_PATH))
        try:
            con.execute("""CREATE TABLE ohlcv_1m (timestamp TIMESTAMP WITH TIME ZONE, symbol VARCHAR,
                           exchange VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                           volume BIGINT, PRIMARY KEY (timestamp, symbol, exchange))""")
            con.execute("INSERT INTO ohlcv_1m VALUES ('2026-01-05 09:15:00+00','RELIANCE','NSE',1,2,0.5,1.5,10)")
        finally:
            con.close()

        from data_pipeline.openalgo_ingestion.publish import build_dataset_card

        card = build_dataset_card("vaibhavfury/StockData")
        for marker in ("Candles | 1", "Symbols | 1",
                       "hf://datasets/vaibhavfury/StockData", "time_bucket",
                       "do not redistribute", "hf_stock_data.py"):
            self.assertIn(marker, card)

    def test_no_data_marks_completed_and_broker_error_marks_failed(self):
        no_data_engine = self._engine({"error_type": "no_data", "message": "holiday"})
        no_data_engine.ingest_date_range(["RELIANCE"], "2026-08-20", "2026-08-20")
        self.assertTrue(no_data_engine.is_chunk_completed("RELIANCE", "2026-08-20", "2026-08-20"))

        error_engine = self._engine({"error_type": "invalid_symbol", "message": "bad symbol"})
        with patch.object(error_engine, "_fetch_from_broker", side_effect=RuntimeError("invalid_symbol: bad symbol")):
            error_engine.ingest_date_range(["BROKEN"], "2026-08-20", "2026-08-20")
        self.assertFalse(error_engine.is_chunk_completed("BROKEN", "2026-08-20", "2026-08-20"))
