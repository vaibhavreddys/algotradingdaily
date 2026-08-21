"""Parquet archival and timeframe aggregation for the OHLCV store."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from . import settings

logger = logging.getLogger(__name__)

AGGREGATES: dict[str, str] = {
    "ohlcv_5m": "5 minutes",
    "ohlcv_15m": "15 minutes",
    "ohlcv_1h": "1 hour",
    "ohlcv_1d": "1 day",
}

# time_bucket() floors TIMESTAMPTZ values on the UTC timeline, which shifts
# hour/day boundaries away from market-local walls (IST = UTC+05:30). Bucketing
# on the naive IST wall clock first keeps buckets aligned to exchange sessions;
# converting back yields a TIMESTAMPTZ stamped at the true IST boundary.
BUCKET_TZ = "Asia/Kolkata"


def _bucket_expr(bucket: str) -> str:
    return (
        f"(time_bucket(INTERVAL '{bucket}', timestamp AT TIME ZONE '{BUCKET_TZ}') "
        f"AT TIME ZONE '{BUCKET_TZ}')"
    )


def _load_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("DuckDB support is not installed.") from exc
    return duckdb


def archive_root() -> Path:
    root = settings.ARCHIVE_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def export_all() -> Path:
    """Idempotent full export to partitioned Parquet (year/month)."""
    duckdb = _load_duckdb()
    root = archive_root()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(settings.DB_PATH))
    try:
        con.execute(
            f"""
            COPY (
                SELECT timestamp, symbol, open, high, low, close, volume, exchange,
                       YEAR(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') AS year,
                       MONTH(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') AS month
                FROM ohlcv_1m
            ) TO '{root}' (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (year, month))
            """
        )
    finally:
        con.close()
    logger.info("Exported archive to %s", root)
    return root


def count_archive_rows() -> int:
    duckdb = _load_duckdb()
    root = archive_root()
    con = duckdb.connect(str(settings.DB_PATH), read_only=True)
    try:
        row = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{root}/**/*.parquet', hive_partitioning=true)"
        ).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0


def build_all_aggregates() -> None:
    """Materialize 5m/15m/1h/1d tables from ohlcv_1m."""
    duckdb = _load_duckdb()
    con = duckdb.connect(str(settings.DB_PATH))
    try:
        for out_table, bucket in AGGREGATES.items():
            con.execute(
                f"""
                CREATE OR REPLACE TABLE {out_table} AS
                SELECT {_bucket_expr(bucket)} AS timestamp,
                       symbol, exchange,
                       arg_min(open, timestamp)  AS open,
                       max(high)                 AS high,
                       min(low)                  AS low,
                       arg_max(close, timestamp) AS close,
                       sum(volume)               AS volume
                FROM ohlcv_1m
                GROUP BY 1, 2, 3
                """
            )
            logger.info("Built %s", out_table)
    finally:
        con.close()


def checkpoint() -> None:
    """Flush WAL to disk before file-level backups."""
    duckdb = _load_duckdb()
    con = duckdb.connect(str(settings.DB_PATH))
    try:
        con.execute("CHECKPOINT")
    finally:
        con.close()
