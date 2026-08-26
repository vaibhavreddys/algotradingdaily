"""
One-Click Incremental Delta Ingestion Tool for DuckDB on VPS.
Automatically detects the latest timestamp in the database and ingests
only the missing 1-minute bars up to the current moment.
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

    # Check latest timestamp recorded in DuckDB
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        max_ts = con.execute("SELECT MAX(timestamp) FROM ohlcv_1m").fetchone()[0]
    except Exception:
        max_ts = None
    con.close()

    if max_ts:
        start_date = (max_ts + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"Current Latest Recorded Bar : {max_ts}")
    else:
        start_date = "2025-10-20"
        print("Database is empty. Ingesting full history...")

    today = datetime.date.today()
    end_date = today.strftime("%Y-%m-%d")

    print(f"Target Ingestion Window      : {start_date} -> {end_date}")

    if max_ts and start_date > end_date:
        print("\n✅ Database is already 100% up-to-date with today! (Zero missing bars).")
        return

    # Ingest for all universe constituents
    symbols = get_symbols_for_universe(CONFIG.UNIVERSE)
    print(f"Universe Constituents ({CONFIG.UNIVERSE}) : {len(symbols)} Stocks")
    print("-----------------------------------------------------")

    engine = ThrottledIngestionEngine()
    engine.ingest_date_range(symbols, start_date=start_date, end_date=end_date)

    print("\n🎉 Incremental update complete! DuckDB is now updated to the current date.")

if __name__ == "__main__":
    update_duckdb()
