#!/usr/bin/env python3
"""Standalone helper for the private Hugging Face dataset vaibhavfury/StockData.

Requires: pip install huggingface_hub duckdb pandas
Auth:     export HF_TOKEN=hf_xxx   (token needs read access to the dataset)

Modes:
  query    Stream/filter data directly over HTTP without downloading.
  parquet  Download raw partitioned Parquet files locally.
  duckdb   Download + materialize a single local .duckdb for offline use.

Examples:
  python hf_stock_data.py query
  python hf_stock_data.py query --sql "SELECT symbol, COUNT(*) FROM {glob} GROUP BY 1"
  python hf_stock_data.py parquet --out ./stockdata --months 2025-10 2025-11
  python hf_stock_data.py duckdb --out ./stockdata --symbol RELIANCE --aggregates
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_DEFAULT = "vaibhavfury/StockData"
GLOB = "hf://datasets/{repo}/data/year=*/month=*/*.parquet"


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def get_token(args) -> str | None:
    token = args.token or os.getenv("HF_TOKEN")
    if not token:
        die("HF_TOKEN is not set. Export a Hugging Face token with read access:\n"
            "  export HF_TOKEN=hf_xxx")
    return token


def connect_duckdb(token: str):
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs", )
    con.execute("LOAD httpfs")
    con.execute("CREATE SECRET (TYPE huggingface, TOKEN ?)", [token])
    con.execute("SET TimeZone='Asia/Kolkata'")
    return con


def mode_query(args) -> int:
    con = connect_duckdb(get_token(args))
    glob = GLOB.format(repo=args.repo)
    sql = args.sql.replace("{glob}", f"read_parquet('{glob}', hive_partitioning=true)")
    frame = con.execute(sql).fetchdf()
    if frame.empty:
        print("(no rows)")
    else:
        print(frame.to_string(index=False, max_rows=args.max_rows))
    return 0


def month_patterns(months: list[str] | None) -> list[str]:
    if not months:
        return ["data/**"]
    patterns = []
    for m in months:
        year, month = m.split("-")
        patterns.append(f"data/year={int(year)}/month={int(month)}/*")
    return patterns


def download_snapshot(repo_id: str, out_dir: Path, months: list[str] | None, token: str) -> Path:
    from huggingface_hub import snapshot_download

    target = out_dir / "data"
    path = snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        allow_patterns=month_patterns(months),
        local_dir=target,
        token=token,
    )
    print(f"downloaded to {path}")
    return Path(path)


def mode_parquet(args) -> int:
    download_snapshot(args.repo, Path(args.out), args.months, get_token(args))
    return 0


def build_local_duckdb(db_path: Path, parquet_glob: str, symbol: str | None, aggregates: bool) -> None:
    import duckdb

    con = duckdb.connect(str(db_path))
    try:
        where = "WHERE symbol = ?" if symbol else ""
        params = [symbol] if symbol else []
        con.execute(f"""
            CREATE OR REPLACE TABLE ohlcv_1m AS
            SELECT timestamp, symbol, exchange, open, high, low, close, volume
            FROM read_parquet('{parquet_glob}', hive_partitioning=true)
            {where} ORDER BY timestamp, symbol
        """, params)
        n = con.execute("SELECT COUNT(*), COUNT(DISTINCT symbol) FROM ohlcv_1m").fetchone()
        print(f"ohlcv_1m: {n[0]:,} rows, {n[1]} symbols")

        con.execute("""
            CREATE OR REPLACE VIEW ohlcv_1m_ist AS
            SELECT timestamp AT TIME ZONE 'Asia/Kolkata' AS ts_ist,
                   symbol, exchange, open, high, low, close, volume
            FROM ohlcv_1m
        """)

        if aggregates:
            buckets = {"ohlcv_5m": "5 minutes", "ohlcv_15m": "15 minutes",
                       "ohlcv_1h": "1 hour", "ohlcv_1d": "1 day"}
            for table, bucket in buckets.items():
                con.execute(f"""
                    CREATE OR REPLACE TABLE {table} AS
                    SELECT time_bucket(INTERVAL '{bucket}', timestamp AT TIME ZONE 'Asia/Kolkata')
                           AT TIME ZONE 'Asia/Kolkata' AS timestamp,
                           symbol, exchange,
                           arg_min(open, timestamp) AS open, max(high) AS high,
                           min(low) AS low, arg_max(close, timestamp) AS close,
                           sum(volume) AS volume
                    FROM ohlcv_1m GROUP BY 1, symbol, exchange
                """)
                rows = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"{table}: {rows:,} rows")
    finally:
        con.close()


def mode_duckdb(args) -> int:
    out = Path(args.out)
    local_root = download_snapshot(args.repo, out, args.months, get_token(args))
    glob = str(local_root / "data" / "year=*" / "month=*" / "*.parquet")
    db_path = out / "backtest_data.duckdb"
    print(f"building {db_path} ...")
    build_local_duckdb(db_path, glob, (args.symbol or "").upper() or None, args.aggregates)
    print(f"done -> {db_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download or stream the StockData HF dataset")
    parser.add_argument("mode", choices=("query", "parquet", "duckdb"))
    parser.add_argument("--repo", default=os.getenv("OPENALGO_HF_REPO", REPO_DEFAULT))
    parser.add_argument("--token", help="HF token (defaults to $HF_TOKEN)")
    parser.add_argument("--out", default="./stockdata", help="target directory for downloads")
    parser.add_argument("--months", nargs="*", metavar="YYYY-MM",
                        help="restrict to specific months, e.g. 2025-10 2026-01")
    parser.add_argument("--symbol", help="keep only this symbol when building the local DuckDB")
    parser.add_argument("--aggregates", action="store_true",
                        help="also build ohlcv_5m/15m/1h/1d tables in duckdb mode")
    parser.add_argument("--sql", help="SQL for query mode; use {glob} as the parquet table reference")
    parser.add_argument("--max-rows", type=int, default=50)
    args = parser.parse_args(argv)

    if args.mode == "query":
        return mode_query(args)
    if args.mode == "parquet":
        return mode_parquet(args)
    return mode_duckdb(args)


if __name__ == "__main__":
    raise SystemExit(main())
