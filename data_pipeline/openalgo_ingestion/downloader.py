"""Crash-safe, throttled OpenAlgo 1-minute historical-data ingestion."""

import datetime as dt
import logging
import sqlite3
import time
from collections.abc import Mapping
from typing import Any, Callable, Iterable

import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from . import settings

logger = logging.getLogger(__name__)


class BrokerHistoryError(RuntimeError):
    """An unsuccessful response returned by the OpenAlgo history endpoint."""


def _load_openalgo_client() -> Callable[..., Any]:
    try:
        from openalgo import api
    except ImportError as exc:
        raise RuntimeError("OpenAlgo support is not installed. Run `pip install -r requirements.txt`.") from exc
    return api


def _load_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("DuckDB support is not installed. Run `pip install -r requirements.txt`.") from exc
    return duckdb


class ThrottledIngestionEngine:
    """Download and persist broker candles without touching the live OpenAlgo DB."""

    def __init__(self, client: Any = None, duckdb_module: Any = None) -> None:
        self._duckdb = duckdb_module or _load_duckdb()
        self.client = client or _load_openalgo_client()(api_key=settings.OPENALGO_API_KEY, host=settings.OPENALGO_HOST)
        self._init_duckdb()
        self._init_state_db()

    def _init_duckdb(self) -> None:
        settings.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        con = self._duckdb.connect(str(settings.DB_PATH))
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS ohlcv_1m (
                    timestamp TIMESTAMP WITH TIME ZONE,
                    symbol VARCHAR,
                    exchange VARCHAR,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    volume BIGINT,
                    PRIMARY KEY (timestamp, symbol, exchange)
                )
                """
            )
        finally:
            con.close()

    def _init_state_db(self) -> None:
        settings.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(settings.STATE_DB_PATH) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS chunk_state (
                    symbol TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (symbol, start_date, end_date)
                )
                """
            )

    @staticmethod
    def _format_symbol(symbol: str) -> str:
        clean_symbol = symbol.strip().upper()
        if settings.SHOONYA_APPEND_EQ and settings.EXCHANGE == "NSE" and not clean_symbol.endswith("-EQ"):
            return f"{clean_symbol}-EQ"
        return clean_symbol

    @staticmethod
    def generate_date_range_chunks(start_date: str, end_date: str) -> list[tuple[str, str]]:
        start = dt.date.fromisoformat(start_date)
        end = dt.date.fromisoformat(end_date)
        if start > end:
            raise ValueError("start-date cannot be greater than end-date")

        chunks: list[tuple[str, str]] = []
        current_start = start
        while current_start <= end:
            current_end = min(current_start + dt.timedelta(days=settings.CHUNK_SIZE_DAYS - 1), end)
            chunks.append((current_start.isoformat(), current_end.isoformat()))
            current_start = current_end + dt.timedelta(days=1)
        return chunks

    @classmethod
    def generate_chunks(cls, total_days: int, today: dt.date | None = None) -> list[tuple[str, str]]:
        if total_days <= 0:
            raise ValueError("lookback_days must be greater than zero")
        end = today or dt.date.today()
        start = end - dt.timedelta(days=total_days - 1)
        return cls.generate_date_range_chunks(start.isoformat(), end.isoformat())

    def is_chunk_completed(self, symbol: str, start: str, end: str) -> bool:
        with sqlite3.connect(settings.STATE_DB_PATH) as con:
            row = con.execute(
                "SELECT status FROM chunk_state WHERE symbol=? AND start_date=? AND end_date=?",
                (symbol, start, end),
            ).fetchone()
        return row is not None and row[0] == "SUCCESS"

    def mark_chunk(self, symbol: str, start: str, end: str, status: str) -> None:
        with sqlite3.connect(settings.STATE_DB_PATH) as con:
            con.execute(
                """
                INSERT INTO chunk_state (symbol, start_date, end_date, status)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol, start_date, end_date) DO UPDATE SET
                    status=excluded.status, last_updated=CURRENT_TIMESTAMP
                """,
                (symbol, start, end, status),
            )

    @retry(
        stop=stop_after_attempt(settings.MAX_RETRIES),
        wait=wait_exponential(multiplier=10, min=10, max=300),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _fetch_from_broker(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        response = self.client.history(
            symbol=self._format_symbol(symbol),
            exchange=settings.EXCHANGE,
            interval=settings.INTERVAL,
            start_date=start,
            end_date=end,
            source="api",
        )
        if isinstance(response, Mapping):
            if response.get("error_type") == "no_data":
                logger.info("No broker data for %s [%s to %s]", symbol, start, end)
                return pd.DataFrame()
            raise BrokerHistoryError(f"{response.get('error_type', 'unknown_error')}: {response.get('message', 'OpenAlgo returned an error response')}")
        if not isinstance(response, pd.DataFrame):
            raise BrokerHistoryError(f"Unexpected history response type: {type(response).__name__}")
        if response.empty:
            return pd.DataFrame()

        frame = response.reset_index()
        frame.columns = [str(column).lower() for column in frame.columns]
        if "timestamp" not in frame.columns and "index" in frame.columns:
            frame = frame.rename(columns={"index": "timestamp"})
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required.difference(frame.columns)
        if missing:
            raise BrokerHistoryError(f"History response for {symbol} is missing columns: {sorted(missing)}")
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame["symbol"] = symbol.strip().upper()
        frame["exchange"] = settings.EXCHANGE
        # Clip negative volumes (closing-auction correction artifacts from broker feed)
        frame["volume"] = frame["volume"].fillna(0).clip(lower=0).astype("int64")
        return frame[["timestamp", "symbol", "exchange", "open", "high", "low", "close", "volume"]]

    def _store_frame(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        con = self._duckdb.connect(str(settings.DB_PATH))
        try:
            con.execute("INSERT OR IGNORE INTO ohlcv_1m BY NAME SELECT * FROM frame")
        finally:
            con.close()

    def ingest(self, symbols: Iterable[str], chunks: Iterable[tuple[str, str]]) -> None:
        settings.validate_settings()
        symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        chunks = list(chunks)
        total = len(symbols) * len(chunks)
        for number, (symbol, (start, end)) in enumerate(
            ((symbol, chunk) for chunk in chunks for symbol in symbols), start=1
        ):
            if self.is_chunk_completed(symbol, start, end):
                logger.info("[%d/%d] Skip %s [%s to %s]: already complete", number, total, symbol, start, end)
                continue
            try:
                frame = self._fetch_from_broker(symbol, start, end)
                self._store_frame(frame)
                self.mark_chunk(symbol, start, end, "SUCCESS")
                logger.info("[%d/%d] Saved %d rows for %s [%s to %s]", number, total, len(frame), symbol, start, end)
            except Exception as exc:
                self.mark_chunk(symbol, start, end, f"FAILED: {exc}")
                logger.error("[%d/%d] Failed %s [%s to %s]: %s", number, total, symbol, start, end, exc)
            time.sleep(settings.DELAY_SECONDS)

    def ingest_date_range(self, symbols: Iterable[str], start_date: str, end_date: str) -> None:
        self.ingest(symbols, self.generate_date_range_chunks(start_date, end_date))

    def ingest_index(self, symbols: Iterable[str], lookback_days: int) -> None:
        self.ingest(symbols, self.generate_chunks(lookback_days))
