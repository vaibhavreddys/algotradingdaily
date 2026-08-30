"""Read-only access to OpenAlgo-ingested historical candles."""
from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

from . import settings

logger = logging.getLogger(__name__)

ALLOWED_TABLES = {"ohlcv_1m", "ohlcv_5m", "ohlcv_15m", "ohlcv_1h", "ohlcv_1d"}


def _load_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("DuckDB support is not installed. Run `pip install -r requirements.txt`.") from exc
    return duckdb


class BacktestDataReader:
    """Return long OHLCV frames or vectorbt-compatible wide close matrices."""

    def __init__(self, db_path: Optional[str] = None, duckdb_module: Any = None) -> None:
        self.db_path = db_path or str(settings.DB_PATH)
        self._duckdb = duckdb_module

    def _duck(self) -> Any:
        return self._duckdb or _load_duckdb()

    def _connect(self, duckdb: Any):
        return duckdb.connect(self.db_path, read_only=True)

    @staticmethod
    def _normalize_timestamp(series: pd.Series) -> pd.Series:
        ts = pd.to_datetime(series, utc=True)
        return ts.dt.tz_convert("Asia/Kolkata")

    def get_full_dataframe(
        self,
        symbol: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        table: str = "ohlcv_1m",
    ) -> pd.DataFrame:
        if table not in ALLOWED_TABLES:
            raise ValueError(f"Unknown table: {table}. Allowed: {sorted(ALLOWED_TABLES)}")
        duckdb = self._duck()
        query = f"SELECT timestamp, symbol, open, high, low, close, volume FROM {table} WHERE 1=1"
        params: list[str] = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.strip().upper())
        if start_date:
            # Date strings denote whole IST days; pin conversions to IST so
            # filtering is deterministic regardless of the server session TZ.
            query += " AND timestamp >= (CAST(? AS TIMESTAMP) AT TIME ZONE 'Asia/Kolkata')"
            params.append(start_date)
        if end_date:
            query += " AND timestamp < ((CAST(? AS TIMESTAMP) AT TIME ZONE 'Asia/Kolkata') + INTERVAL 1 DAY)"
            params.append(end_date)
        query += " ORDER BY timestamp ASC, symbol ASC"

        con = self._connect(duckdb)
        try:
            frame = con.execute(query, params).fetchdf()
        finally:
            con.close()

        if not frame.empty:
            frame["timestamp"] = self._normalize_timestamp(frame["timestamp"])
            frame = frame.set_index("timestamp")
        return frame

    def get_vectorbt_matrix(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        table: str = "ohlcv_1m",
    ) -> pd.DataFrame:
        """Wide close matrix via pandas pivot. Filter by date to limit memory."""
        frame = self.get_full_dataframe(start_date=start_date, end_date=end_date, table=table)
        if frame.empty:
            return pd.DataFrame()
        return frame.pivot(columns="symbol", values="close").sort_index().ffill().bfill()

    def get_close_matrix_sql(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        table: str = "ohlcv_1m",
    ) -> pd.DataFrame:
        """Wide close matrix computed by DuckDB PIVOT (less Pandas memory churn)."""
        if table not in ALLOWED_TABLES:
            raise ValueError(f"Unknown table: {table}. Allowed: {sorted(ALLOWED_TABLES)}")
        duckdb = self._duck()
        where: list[str] = []
        params: list[str] = []
        if start_date:
            where.append("timestamp >= (CAST(? AS TIMESTAMP) AT TIME ZONE 'Asia/Kolkata')")
            params.append(start_date)
        if end_date:
            where.append("timestamp < ((CAST(? AS TIMESTAMP) AT TIME ZONE 'Asia/Kolkata') + INTERVAL 1 DAY)")
            params.append(end_date)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        con = self._connect(duckdb)
        try:
            # DuckDB forbids parameters inside a PIVOT source, so stage the
            # filtered rows as a session-local view before pivoting.
            long_frame = con.execute(
                f"SELECT timestamp, symbol, close FROM {table} {where_sql}", params
            ).fetchdf()
            if long_frame.empty:
                return pd.DataFrame()
            con.register("pivot_source", long_frame)
            frame = con.execute(
                """
                PIVOT pivot_source ON symbol USING first(close)
                GROUP BY timestamp ORDER BY timestamp
                """
            ).fetchdf()
        finally:
            con.close()

        if frame.empty:
            return pd.DataFrame()
        frame["timestamp"] = self._normalize_timestamp(frame["timestamp"])
        frame = frame.set_index("timestamp").sort_index()
        frame = frame.rename_axis(columns="symbol")
        return frame

    def get_symbols(self, table: str = "ohlcv_15m") -> list[str]:
        """Return list of distinct symbols present in the given table."""
        if table not in ALLOWED_TABLES:
            raise ValueError(f"Unknown table: {table}. Allowed: {sorted(ALLOWED_TABLES)}")
        duckdb = self._duck()
        with self._connect(duckdb) as conn:
            rows = conn.execute(f"SELECT DISTINCT symbol FROM {table} ORDER BY symbol").fetchall()
            return [r[0] for r in rows]

    def get_stats(self) -> dict:
        duckdb = self._duck()
        con = self._connect(duckdb)
        try:
            summary = con.execute(
                """SELECT COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols,
                          MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts
                   FROM ohlcv_1m"""
            ).fetchdf()
            monthly = con.execute(
                """SELECT date_trunc('month', timestamp) AS month, COUNT(*) AS candles,
                          COUNT(DISTINCT symbol) AS symbols
                   FROM ohlcv_1m GROUP BY 1 ORDER BY 1"""
            ).fetchdf()
        finally:
            con.close()
        return {"summary": summary, "monthly": monthly}
    def get_universe_dataframes(
        self,
        symbols: Optional[list[str]] = None,
        table: str = "ohlcv_15m"
    ) -> dict[str, pd.DataFrame]:
        """
        High-speed single-batch query that loads all universe symbols in ONE database read (~1.5s).
        Returns a dict mapping symbol -> DataFrame with Asia/Kolkata DatetimeIndex and standard OHLCV columns.
        """
        if table not in ALLOWED_TABLES:
            raise ValueError(f"Unknown table: {table}. Allowed: {sorted(ALLOWED_TABLES)}")
            
        duckdb = self._duck()
        con = self._connect(duckdb)
        
        try:
            if symbols:
                clean_syms = [s.replace('.NS', '').replace('^NSEI', 'NIFTY50') for s in symbols]
                in_clause = ", ".join(f"'{s}'" for s in clean_syms)
                query = f"""
                    SELECT symbol, timestamp, open, high, low, close, volume
                    FROM {table}
                    WHERE symbol IN ({in_clause})
                    ORDER BY symbol, timestamp ASC
                """
            else:
                query = f"""
                    SELECT symbol, timestamp, open, high, low, close, volume
                    FROM {table}
                    ORDER BY symbol, timestamp ASC
                """
            df_all = con.execute(query).fetchdf()
        finally:
            con.close()
            
        if df_all.empty:
            return {}
            
        df_all["timestamp"] = self._normalize_timestamp(df_all["timestamp"])
        df_all.columns = [c.capitalize() if c != 'timestamp' else 'timestamp' for c in df_all.columns]
        
        result = {}
        for sym, group in df_all.groupby('Symbol'):
            gdf = group.set_index('timestamp').drop(columns=['Symbol'])
            result[sym] = gdf
            result[f"{sym}.NS"] = gdf
            
        return result


