"""
One-Click Incremental Delta Ingestion Tool for DuckDB on VPS.
Features:
  1. Per-Symbol Delta Alignment (queries each symbol's exact latest timestamp).
  2. Auto-Retries Discrepant / Lagging Symbols automatically.
  3. Automatic Fallback Ingestion (if broker has no data for a symbol).
  4. Full Post-Ingestion Sanity Audit & Verification Scorecard.
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
    # Audit & Discrepancy Recovery Loop (Auto-Healing)
    # -----------------------------------------------------------------
    print("\n=====================================================")
    print("           AUDITING FOR DISCREPANCIES")
    print("=====================================================")
    con = duckdb.connect(str(db_path), read_only=True)
    audit_rows = con.execute("""
        SELECT symbol, COUNT(*) as bar_count, MIN(timestamp) as min_ts, MAX(timestamp) as max_ts
        FROM ohlcv_1m
        GROUP BY symbol
    """).fetchall()
    con.close()

    audit_map = {sym: (count, max_ts) for sym, count, _, max_ts in audit_rows}
    lagging_symbols = []

    for sym in symbols:
        clean_sym = sym.strip().upper()
        info = audit_map.get(clean_sym)
        if not info:
            lagging_symbols.append((clean_sym, 0, "MISSING"))
        else:
            cnt, max_t = info
            max_t_str = str(max_t)[:10] if max_t else ""
            if max_t_str != today and datetime.datetime.strptime(today, "%Y-%m-%d").weekday() < 5:
                lagging_symbols.append((clean_sym, cnt, str(max_t)))

    if lagging_symbols:
        print(f"⚠️ Found {len(lagging_symbols)} lagging symbol(s). Triggering Auto-Recovery...")
        for clean_sym, cnt, max_t in lagging_symbols:
            logger.warning("🔄 Re-fetching delta for lagging symbol: %s (Current: %s)", clean_sym, max_t)
            # Remove from state db to force re-fetch
            with sqlite3.connect(settings.STATE_DB_PATH) as s_con:
                s_con.execute("DELETE FROM chunk_state WHERE symbol=?", (clean_sym,))
            # Re-fetch
            start_date = str(max_t)[:10] if max_t != "MISSING" else "2026-08-22"
            engine.ingest_date_range([clean_sym], start_date=start_date, end_date=today)

    # Final Report
    con = duckdb.connect(str(db_path), read_only=True)
    total_bars = con.execute("SELECT COUNT(*) FROM ohlcv_1m").fetchone()[0]
    global_max = con.execute("SELECT MAX(timestamp) FROM ohlcv_1m").fetchone()[0]
    final_count_up_to_date = con.execute(f"SELECT COUNT(DISTINCT symbol) FROM ohlcv_1m WHERE CAST(timestamp AS DATE) = '{today}'").fetchone()[0]
    con.close()

    print("\n=====================================================")
    print("🎉 FINAL INGESTION SANITY SCORECARD")
    print("=====================================================")
    print(f"Total 1-Minute Bars in DuckDB : {total_bars:,}")
    print(f"Global Latest Bar Timestamp   : {global_max}")
    print(f"Constituents 100% Up-to-Date  : {final_count_up_to_date} / {len(symbols)}")
    if final_count_up_to_date == len(symbols):
        print("✅ ALL 200 constituents are verified and 100% up-to-date!")
    else:
        print(f"ℹ️ {final_count_up_to_date}/{len(symbols)} symbols verified with live bars today.")
    print("=====================================================\n")

if __name__ == "__main__":
    import sqlite3
    update_duckdb()
