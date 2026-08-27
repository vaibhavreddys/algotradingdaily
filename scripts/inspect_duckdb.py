"""
DuckDB Inspector & Sanity Audit Tool for Laptop.
Usage:
  python scripts/inspect_duckdb.py                  # Shows full multi-table audit & recent bars
  python scripts/inspect_duckdb.py RELIANCE         # Shows latest 20 15m & 1m bars for RELIANCE
  python scripts/inspect_duckdb.py --all            # Lists all 200 constituents across timeframes
"""
import sys, os, duckdb, datetime
import pandas as pd

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

pd.set_option('display.max_columns', 10)
pd.set_option('display.width', 1000)

db_path = "market_data/openalgo/backtest_data.duckdb"

if not os.path.exists(db_path):
    print(f"❌ Error: Database file not found at {db_path}")
    print("Run `python scripts/sync_from_vps.py` to sync from VPS first.")
    sys.exit(1)

con = duckdb.connect(db_path, read_only=True)

target_sym = sys.argv[1].upper() if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else None

if target_sym:
    print(f"=====================================================")
    print(f"     DUCKDB INSPECTOR: {target_sym}")
    print(f"=====================================================")
    for tbl in ["ohlcv_15m", "ohlcv_1m"]:
        try:
            print(f"\n--- Latest 10 Bars in {tbl} ({target_sym}) ---")
            df = con.execute(f"SELECT timestamp, open, high, low, close, volume FROM {tbl} WHERE symbol = '{target_sym}' ORDER BY timestamp DESC LIMIT 10").fetchdf()
            print(df.to_string(index=False))
            total = con.execute(f"SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM {tbl} WHERE symbol='{target_sym}'").fetchone()
            print(f"Total: {total[0]:,} bars | Range: {total[1]} to {total[2]}")
        except Exception as e:
            print(f"Table {tbl} error: {e}")
else:
    print("=====================================================")
    print("       MULTI-TIMEFRAME DATABASE SANITY AUDIT")
    print("=====================================================")
    file_size_mb = os.path.getsize(db_path) / (1024 * 1024)
    print(f"Database File : {db_path} ({file_size_mb:.2f} MB)")
    print("-----------------------------------------------------")

    today = datetime.date.today().strftime("%Y-%m-%d")
    tables = ["ohlcv_1m", "ohlcv_5m", "ohlcv_15m", "ohlcv_1h", "ohlcv_1d"]

    for tbl in tables:
        try:
            total_bars = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            distinct_syms = con.execute(f"SELECT COUNT(DISTINCT symbol) FROM {tbl}").fetchone()[0]
            min_ts, max_ts = con.execute(f"SELECT MIN(timestamp), MAX(timestamp) FROM {tbl}").fetchone()
            today_syms = con.execute(f"SELECT COUNT(DISTINCT symbol) FROM {tbl} WHERE CAST(timestamp AS DATE) = '{today}'").fetchone()[0]
            status_icon = "✅" if today_syms == 200 or datetime.datetime.strptime(today, "%Y-%m-%d").weekday() >= 5 else "ℹ️"
            print(f" {status_icon} {tbl:10} | Bars: {total_bars:>10,} | Symbols: {distinct_syms:>3}/200 | Latest: {max_ts} | Today: {today_syms}/200")
        except Exception as e:
            print(f" ❌ {tbl:10} | Error: {e}")

    print("-----------------------------------------------------")
    print("\nSample: Latest 5 15-Minute Bars for RELIANCE (ohlcv_15m):")
    try:
        sample_df = con.execute("SELECT timestamp, open, high, low, close, volume FROM ohlcv_15m WHERE symbol='RELIANCE' ORDER BY timestamp DESC LIMIT 5").fetchdf()
        print(sample_df.to_string(index=False))
    except Exception as e:
        print(f"Could not query ohlcv_15m: {e}")

con.close()
print("=====================================================\n")
