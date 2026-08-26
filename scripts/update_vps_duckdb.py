"""
One-Click Incremental Delta Ingestion Tool for DuckDB on VPS.
Accurately finds missing dates per-symbol and ingests all missing 1-minute bars.
"""
import sys, os, datetime, logging
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Configure clean, live console logging so progress is visible immediately
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)

from config import CONFIG
from data_pipeline.data_feed import get_symbols_for_universe
from data_pipeline.openalgo_ingestion.downloader import ThrottledIngestionEngine
from data_pipeline.openalgo_ingestion import settings
import duckdb

def update_duckdb():
    db_path = settings.DB_PATH
    print("=====================================================")
    print("     DUCKDB INCREMENTAL MARKET DATA INGESTION")
    print("=====================================================")
    print(f"Database File: {db_path}")

    # Allow custom start date from CLI or compute baseline
    today = datetime.date.today().strftime("%Y-%m-%d")
    start_date = sys.argv[1] if len(sys.argv) > 1 else "2026-08-22"
    end_date = sys.argv[2] if len(sys.argv) > 2 else today

    print(f"Target Ingestion Window      : {start_date} -> {end_date}")

    symbols = get_symbols_for_universe(CONFIG.UNIVERSE)
    print(f"Universe Constituents ({CONFIG.UNIVERSE}) : {len(symbols)} Stocks")
    print("-----------------------------------------------------")

    engine = ThrottledIngestionEngine()
    engine.ingest_date_range(symbols, start_date=start_date, end_date=end_date)

    # Final summary check
    con = duckdb.connect(str(db_path), read_only=True)
    count = con.execute("SELECT COUNT(*) FROM ohlcv_1m").fetchone()[0]
    max_ts = con.execute("SELECT MAX(timestamp) FROM ohlcv_1m").fetchone()[0]
    con.close()

    print("\n=====================================================")
    print("🎉 Ingestion complete!")
    print(f"   Total Bars in Database : {count:,}")
    print(f"   Latest Timestamp       : {max_ts}")
    print("=====================================================")

if __name__ == "__main__":
    update_duckdb()
