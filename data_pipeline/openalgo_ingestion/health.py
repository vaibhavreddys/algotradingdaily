"""Database health-check and integrity verification."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import settings

logger = logging.getLogger(__name__)


def _load_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "DuckDB support is not installed. Run `pip install -r requirements.txt`."
        ) from exc
    return duckdb


@dataclass
class HealthReport:
    """Structured result of a full health check."""

    healthy: bool = True
    total_rows: int = 0
    total_symbols: int = 0
    first_timestamp: str = ""
    last_timestamp: str = ""
    null_prices: int = 0
    null_volumes: int = 0
    negative_volumes: int = 0
    negative_prices: int = 0
    inverted_high_low: int = 0
    duplicate_candles: int = 0
    state_total_chunks: int = 0
    state_success: int = 0
    state_failed: int = 0
    failed_chunks: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "══════════════════════════════════════════",
            "  OpenAlgo Ingestion — Health Report",
            "══════════════════════════════════════════",
            f"  Status              : {'✅ HEALTHY' if self.healthy else '❌ ISSUES FOUND'}",
            f"  Total rows          : {self.total_rows:,}",
            f"  Total symbols       : {self.total_symbols}",
            f"  Date range          : {self.first_timestamp} → {self.last_timestamp}",
            "──────────────────────────────────────────",
            f"  NULL prices         : {self.null_prices}",
            f"  NULL volumes        : {self.null_volumes}",
            f"  Negative volumes    : {self.negative_volumes}",
            f"  Negative prices     : {self.negative_prices}",
            f"  Inverted high/low   : {self.inverted_high_low}",
            f"  Duplicate candles   : {self.duplicate_candles}",
            "──────────────────────────────────────────",
            f"  State: total chunks : {self.state_total_chunks}",
            f"  State: success      : {self.state_success}",
            f"  State: failed       : {self.state_failed}",
            "══════════════════════════════════════════",
        ]
        if self.failed_chunks:
            lines.append("\n  Failed chunks (up to 10):")
            for chunk in self.failed_chunks[:10]:
                lines.append(
                    f"    • {chunk['symbol']} [{chunk['start_date']} → {chunk['end_date']}]"
                )
        if self.issues:
            lines.append("\n  ⚠️  Issues detected:")
            for issue in self.issues:
                lines.append(f"    • {issue}")
        return "\n".join(lines)


def run_health_check() -> HealthReport:
    """Execute a full integrity check against DuckDB and the state DB."""
    duckdb = _load_duckdb()
    report = HealthReport()

    # ── 1. DuckDB connectivity & basic stats ──
    try:
        con = duckdb.connect(str(settings.DB_PATH), read_only=True)
    except Exception as exc:
        report.healthy = False
        report.issues.append(f"Cannot open DuckDB: {exc}")
        return report

    try:
        row = con.execute(
            """SELECT COUNT(*), COUNT(DISTINCT symbol),
                      MIN(timestamp), MAX(timestamp)
               FROM ohlcv_1m"""
        ).fetchone()
        report.total_rows = int(row[0])
        report.total_symbols = int(row[1])
        report.first_timestamp = str(row[2]) if row[2] else "N/A"
        report.last_timestamp = str(row[3]) if row[3] else "N/A"
    except Exception as exc:
        report.healthy = False
        report.issues.append(f"Schema query failed (table may not exist): {exc}")
        con.close()
        return report

    # ── 2. Data quality checks ──
    quality = con.execute(
        """SELECT
               COUNT(*) FILTER (WHERE open IS NULL OR high IS NULL
                                     OR low IS NULL OR close IS NULL) AS null_prices,
               COUNT(*) FILTER (WHERE volume IS NULL)                 AS null_volumes,
               COUNT(*) FILTER (WHERE volume < 0)                     AS negative_volumes,
               COUNT(*) FILTER (WHERE open <= 0 OR high <= 0
                                     OR low <= 0 OR close <= 0)       AS negative_prices,
               COUNT(*) FILTER (WHERE high < low)                     AS inverted_hl
           FROM ohlcv_1m"""
    ).fetchone()

    report.null_prices = int(quality[0])
    report.null_volumes = int(quality[1])
    report.negative_volumes = int(quality[2])
    report.negative_prices = int(quality[3])
    report.inverted_high_low = int(quality[4])

    # ── 3. Duplicate detection (should be 0 due to PK) ──
    dup = con.execute(
        """SELECT COUNT(*) FROM (
               SELECT timestamp, symbol, exchange, COUNT(*) AS cnt
               FROM ohlcv_1m
               GROUP BY timestamp, symbol, exchange
               HAVING cnt > 1
           )"""
    ).fetchone()
    report.duplicate_candles = int(dup[0])

    # ── 4. Per-symbol candle count (spot anomalies) ──
    symbol_stats = con.execute(
        """SELECT symbol, COUNT(*) AS candles
           FROM ohlcv_1m
           GROUP BY symbol
           ORDER BY candles ASC
           LIMIT 5"""
    ).fetchdf()
    if not symbol_stats.empty:
        min_candles = int(symbol_stats.iloc[0]["candles"])
        if min_candles < 10 and report.total_symbols > 1:
            report.issues.append(
                f"Symbol '{symbol_stats.iloc[0]['symbol']}' has only "
                f"{min_candles} candles — possible incomplete download"
            )

    con.close()

    # ── 5. SQLite state DB checks ──
    try:
        scon = sqlite3.connect(settings.STATE_DB_PATH)
        state_counts = scon.execute(
            """SELECT
                   COUNT(*),
                   SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN status != 'SUCCESS' THEN 1 ELSE 0 END)
               FROM chunk_state"""
        ).fetchone()
        report.state_total_chunks = int(state_counts[0])
        report.state_success = int(state_counts[1] or 0)
        report.state_failed = int(state_counts[2] or 0)

        if report.state_failed > 0:
            rows = scon.execute(
                """SELECT symbol, start_date, end_date, status
                   FROM chunk_state
                   WHERE status != 'SUCCESS'
                   ORDER BY last_updated DESC
                   LIMIT 10"""
            ).fetchall()
            report.failed_chunks = [
                {"symbol": r[0], "start_date": r[1], "end_date": r[2], "status": r[3]}
                for r in rows
            ]
        scon.close()
    except Exception as exc:
        report.issues.append(f"Cannot read state DB: {exc}")

    # ── 6. Final verdict ──
    if report.null_prices > 0:
        report.healthy = False
        report.issues.append(f"{report.null_prices} rows with NULL prices")
    if report.negative_volumes > 0:
        report.healthy = False
        report.issues.append(f"{report.negative_volumes} rows with negative volume (closing-auction artifacts)")
    if report.negative_prices > 0:
        report.healthy = False
        report.issues.append(f"{report.negative_prices} rows with negative/zero prices")
    if report.inverted_high_low > 0:
        report.healthy = False
        report.issues.append(f"{report.inverted_high_low} rows where high < low")
    if report.duplicate_candles > 0:
        report.healthy = False
        report.issues.append(f"{report.duplicate_candles} duplicate candles detected")

    return report
