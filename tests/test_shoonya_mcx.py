import datetime as dt
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


HAS_MCX_DEPS = all(
    importlib.util.find_spec(name) for name in ("duckdb", "tenacity", "pyotp", "NorenRestApiPy")
)


class FakeNorenApi:
    """Stub of NorenApi serving synthetic GOLD candles from a fixed boundary."""

    def __init__(self, oldest: dt.datetime, ist: dt.timezone):
        self.oldest = oldest
        self.ist = ist
        self.calls = 0

    def login(self, **kwargs):
        return {"stat": "Ok", "susertoken": "test-token"}

    def get_limits(self):
        return {"stat": "Ok"}

    def searchscrip(self, exchange, searchtext):
        return {"stat": "Ok", "scrip": [
            {"token": "10133", "tsym": "GOLD25SEPFUT", "instname": "FUT", "expiry": "2025-09-25"},
            {"token": "77777", "tsym": "GOLDGUINEA25SEPFUT", "instname": "FUT", "expiry": "2025-08-25"},
        ]}

    def get_time_price_series(self, exchange, token, starttime, endtime, interval):
        self.calls += 1
        start = dt.datetime.fromtimestamp(starttime, tz=self.ist)
        end = dt.datetime.fromtimestamp(endtime, tz=self.ist)
        if end < self.oldest:
            return None  # error document for pre-history windows
        rows = []
        cursor = max(start, self.oldest)
        while cursor <= end and len(rows) < 1000:
            if cursor.hour >= 9:
                rows.append({
                    "time": cursor.strftime("%d-%b-%Y %H:%M:%S"),
                    "ot": "72000.00", "hi": "72100.00", "lo": "71950.00",
                    "cl": "72050.00", "ap": "72020.00", "vol": "12",
                })
            cursor += dt.timedelta(minutes=1)
        return rows


@unittest.skipUnless(HAS_MCX_DEPS, "Shoonya MCX ingestion dependencies are not installed")
class TestShoonyaMCXIngestion(unittest.TestCase):
    def setUp(self):
        from data_pipeline.shoonya_mcx import settings

        self.settings = settings
        self.temp_dir = tempfile.TemporaryDirectory()
        storage = Path(self.temp_dir.name)
        self.oldest = dt.datetime(2025, 7, 1, tzinfo=settings.IST)
        self.settings_patch = patch.multiple(
            settings,
            STORAGE_DIR=storage,
            DB_PATH=storage / "mcx_historical_data.duckdb",
            LOG_PATH=storage / "ingestion.log",
            SHOONYA_USER_ID="U1",
            SHOONYA_PASSWORD="P1",
            SHOONYA_TOTP_KEY="JBSWY3DPEHPK3PXP",
            SHOONYA_VENDOR_CODE="V1",
            SHOONYA_API_SECRET="S1",
            DELAY_SECONDS=0,
            PROBE_DELAY_SECONDS=0,
            SEARCH_START=dt.date(2015, 1, 1),
            CHUNK_SIZE_DAYS=30,
            MAX_CANDLES_PER_REQUEST=1000,
        )
        self.settings_patch.start()

        from data_pipeline.shoonya_mcx.downloader import MCXIngestionEngine

        self.api = FakeNorenApi(self.oldest, settings.IST)
        self.engine = MCXIngestionEngine(api=self.api)

    def tearDown(self):
        self.settings_patch.stop()
        self.temp_dir.cleanup()

    def test_binary_search_finds_exact_boundary(self):
        found = self.engine.find_oldest_available_date("GOLD", "MCX", "10133")
        self.assertEqual(found, self.oldest.date())

    def test_token_resolution_picks_front_month_future(self):
        exchange, token = self.engine._token_for("GOLD")
        self.assertEqual(exchange, "MCX")
        self.assertEqual(token, "10133")  # nearest expiry, not GOLDGUINEA

    def test_ingest_upserts_idempotently(self):
        rows = self.engine.ingest_commodity("GOLD")
        self.assertGreater(rows, 0)
        con = self.engine._duckdb.connect(str(self.settings.DB_PATH))
        try:
            duplicates = con.execute(
                "SELECT COUNT(*) FROM (SELECT symbol, timestamp FROM commodity_prices "
                "GROUP BY 1, 2 HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            columns = {row[0]: row[1] for row in con.execute("DESCRIBE commodity_prices").fetchall()}
        finally:
            con.close()
        self.assertEqual(duplicates, 0)
        self.assertEqual(
            columns,
            {"symbol": "VARCHAR", "timestamp": "TIMESTAMP", "open": "DOUBLE",
             "high": "DOUBLE", "low": "DOUBLE", "close": "DOUBLE", "volume": "BIGINT"},
        )

    def test_second_run_resumes_from_last_chunk(self):
        self.engine.ingest_commodity("GOLD")
        calls_after_first = self.api.calls
        self.engine.ingest_commodity("GOLD")
        # Cached boundary + resume point: only the overlap day is re-fetched.
        self.assertLess(self.api.calls - calls_after_first, 10)

    def test_adaptive_bisection_beats_candle_cap(self):
        # A 30-day window at 900 session-minutes/day saturates the 1000-candle
        # cap; the engine must bisect and still return the full range.
        frame = self.engine._fetch_window(
            "MCX", "10133", "GOLD", self.oldest.date(), self.oldest.date() + dt.timedelta(days=30)
        )
        self.assertGreater(len(frame), self.settings.MAX_CANDLES_PER_REQUEST)


if __name__ == "__main__":
    unittest.main()
