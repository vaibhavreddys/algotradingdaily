import datetime as dt
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


HAS_MCX_DEPS = all(
    importlib.util.find_spec(name) for name in ("duckdb", "tenacity", "pandas", "requests")
)


def _epoch(naive_ist: dt.datetime) -> int:
    """ssboe-style UTC epoch for a naive IST wall-clock datetime."""
    return int(naive_ist.replace(tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))).timestamp())


class FakeShoonyaApi:
    """Stub of ShoonyaHttpClient serving synthetic GOLD candles from a fixed boundary."""

    def __init__(self, oldest: dt.datetime, ist: dt.timezone):
        self.oldest = oldest
        self.ist = ist
        self.calls = 0
        self.session_token = "test-token"

    def get_limits(self):
        return {"stat": "Ok"}

    def get_time_price_series(self, exchange, token, starttime, endtime, interval):
        self.calls += 1
        start = dt.datetime.fromtimestamp(starttime, tz=self.ist)
        end = dt.datetime.fromtimestamp(endtime, tz=self.ist)
        if end < self.oldest:
            return {"stat": "Not_Ok", "emsg": "no data"}  # error document for pre-history windows
        rows = []
        cursor = max(start, self.oldest)
        while cursor <= end and len(rows) < 1000:
            if cursor.hour >= 9:
                rows.append({
                    "ssboe": _epoch(cursor),
                    "time": cursor.strftime("%d-%m-%Y %H:%M:%S"),
                    "into": "72000.00", "inth": "72100.00", "intl": "71950.00",
                    "intc": "72050.00", "intv": "12",
                })
            cursor += dt.timedelta(minutes=1)
        return rows


class FakeScripCache:
    """Stub of ScripMasterCache with a GOLD + GOLDGUINEA + GOLDM master."""

    def rows(self):
        return [
            {"Token": "10133", "Symbol": "GOLD", "TradingSymbol": "GOLD25OCTFUT",
             "Expiry": "05-OCT-2025", "Instrument": "FUTCOM"},
            {"Token": "77777", "Symbol": "GOLDGUINEA", "TradingSymbol": "GOLDGUINEA25SEPFUT",
             "Expiry": "25-SEP-2025", "Instrument": "FUTCOM"},
            {"Token": "88888", "Symbol": "GOLDM", "TradingSymbol": "GOLDM25SEPFUT",
             "Expiry": "25-SEP-2025", "Instrument": "FUTCOM"},
        ]


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
            SHOONYA_SUSERTOKEN="",
            OPENALGO_DIR="",
            DELAY_SECONDS=0,
            PROBE_DELAY_SECONDS=0,
            SEARCH_START=dt.date(2015, 1, 1),
            CHUNK_SIZE_DAYS=30,
            MAX_CANDLES_PER_REQUEST=1000,
        )
        self.settings_patch.start()

        from data_pipeline.shoonya_mcx.downloader import MCXIngestionEngine

        self.api = FakeShoonyaApi(self.oldest, settings.IST)
        self.engine = MCXIngestionEngine(api=self.api, scrip_cache=FakeScripCache())

    def tearDown(self):
        self.settings_patch.stop()
        self.temp_dir.cleanup()

    def test_binary_search_finds_exact_boundary(self):
        found = self.engine.find_oldest_available_date("GOLD", "MCX", "10133")
        self.assertEqual(found, self.oldest.date())

    def test_token_resolution_picks_front_month_future(self):
        exchange, token = self.engine._token_for("GOLD")
        self.assertEqual(exchange, "MCX")
        self.assertEqual(token, "10133")  # exact Symbol stem, not GOLDGUINEA/GOLDM

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
            sample = con.execute(
                "SELECT timestamp, close FROM commodity_prices ORDER BY timestamp LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        self.assertEqual(duplicates, 0)
        self.assertEqual(
            columns,
            {"symbol": "VARCHAR", "timestamp": "TIMESTAMP", "open": "DOUBLE",
             "high": "DOUBLE", "low": "DOUBLE", "close": "DOUBLE", "volume": "BIGINT"},
        )
        # ssboe epochs must land as naive IST wall-clock timestamps.
        self.assertEqual(sample[0], self.oldest.replace(hour=9, tzinfo=None))
        self.assertEqual(sample[1], 72050.0)

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

    def test_stale_zero_ohlc_rows_are_dropped(self):
        from data_pipeline.shoonya_mcx.downloader import MCXIngestionEngine

        rows = [
            {"ssboe": _epoch(self.oldest), "time": "01-07-2025 09:15:00",
             "into": "0", "inth": "0", "intl": "0", "intc": "0", "intv": "0"},
            {"ssboe": _epoch(self.oldest + dt.timedelta(minutes=1)), "time": "01-07-2025 09:16:00",
             "into": "100", "inth": "101", "intl": "99", "intc": "100", "intv": "5"},
        ]
        frame = MCXIngestionEngine._rows_to_frame(rows, "GOLD")
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["close"], 100.0)


if __name__ == "__main__":
    unittest.main()
