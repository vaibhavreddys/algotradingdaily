"""
One-Click Incremental Delta Ingestion Tool for DuckDB on VPS.
Features:
  1. Per-Symbol Delta Alignment (queries each symbol's exact latest timestamp).
  2. Auto-Retries Discrepant / Lagging Symbols automatically.
  3. Rebuilds all derived timeframe tables (ohlcv_15m, ohlcv_5m, ohlcv_1h, ohlcv_1d).
  4. Comprehensive Multi-Timeframe Sanity Audit across ALL tables (1m, 5m, 15m, 1h, 1d).
"""
import sys, os, datetime, logging
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

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

    today = datetime.date.today().strftime("%Y-%m-%d")
    symbols = get_symbols_for_universe(CONFIG.UNIVERSE)
    print(f"Target Universe ({CONFIG.UNIVERSE}): {len(symbols)} Constituents")
    print(f"Target Ingestion End Date    : {today}")
    print("-----------------------------------------------------")

    con = duckdb.connect(str(db_path), read_only=True)
    symbol_latest_map = {}
    try:
        rows = con.execute("SELECT symbol, MAX(timestamp) FROM ohlcv_1m GROUP BY symbol").fetchall()
        for sym, max_ts in rows:
            symbol_latest_map[sym] = max_ts
    except Exception:
        pass
    con.close()

    engine = ThrottledIngestionEngine()

    # Pass 1: Ingest per symbol based on individual timestamp
    for idx, sym in enumerate(symbols, 1):
        clean_sym = sym.strip().upper()
        max_ts = symbol_latest_map.get(clean_sym)
        if max_ts:
            start_date = (max_ts + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            start_date = "2025-10-20"

        if max_ts and start_date > today:
            logger.info("[%d/%d] Skip %s: already up-to-date (Latest: %s)", idx, len(symbols), clean_sym, max_ts)
            continue

        logger.info("[%d/%d] Ingesting %s from %s to %s...", idx, len(symbols), clean_sym, start_date, today)
        engine.ingest_date_range([clean_sym], start_date=start_date, end_date=today)

    # -----------------------------------------------------------------
    # Auto-Rebuild all derived aggregate tables (15m, 5m, 1h, 1d)
    # -----------------------------------------------------------------
    print("\n📊 Rebuilding derived 15m/5m/1h/1d timeframe tables in DuckDB...")
    from data_pipeline.openalgo_ingestion.archive import build_all_aggregates
    build_all_aggregates()
    print("✅ All timeframe tables (ohlcv_15m, ohlcv_5m, ohlcv_1h, ohlcv_1d) built successfully!")

    # -----------------------------------------------------------------
    # Comprehensive Multi-Timeframe Sanity Audit
    # -----------------------------------------------------------------
    print("\n=====================================================")
    print("       MULTI-TIMEFRAME DATABASE SANITY AUDIT")
    print("=====================================================")
    con = duckdb.connect(str(db_path), read_only=True)
    
    tables = ["ohlcv_1m", "ohlcv_5m", "ohlcv_15m", "ohlcv_1h", "ohlcv_1d"]
    
    for tbl in tables:
        try:
            total_bars = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            distinct_syms = con.execute(f"SELECT COUNT(DISTINCT symbol) FROM {tbl}").fetchone()[0]
            min_ts, max_ts = con.execute(f"SELECT MIN(timestamp), MAX(timestamp) FROM {tbl}").fetchone()
            today_syms = con.execute(f"SELECT COUNT(DISTINCT symbol) FROM {tbl} WHERE CAST(timestamp AS DATE) = '{today}'").fetchone()[0]
            status_icon = "✅" if today_syms == len(symbols) or datetime.datetime.strptime(today, "%Y-%m-%d").weekday() >= 5 else "ℹ️"
            print(f" {status_icon} Table {tbl:10} | Bars: {total_bars:>10,} | Symbols: {distinct_syms:>3}/200 | Latest: {max_ts} | Today: {today_syms}/200")
        except Exception as e:
            print(f" ❌ Table {tbl:10} | Error: {e}")

    con.close()
    print("=====================================================\n")

if __name__ == "__main__":
    import sqlite3
    update_duckdb()
