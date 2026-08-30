"""Shoonya (Noren) 1-minute MCX historical ingestion into DuckDB.

Pipeline per commodity:

1. Authenticate once with TOTP-based 2FA (``QuickAuth``).
2. Resolve the current front-month futures token via ``SearchScrip``.
3. Binary-search the earliest date Shoonya serves minute candles for.
4. Walk forward in date chunks, pulling TPSeries 1-minute candles and
   adaptively bisecting any chunk that saturates the per-request candle cap.
5. Upsert into ``commodity_prices`` (composite PK on symbol+timestamp makes
   every run idempotent, so overlapping pulls never duplicate rows).

The engine is deliberately parameterised by its symbol registry and exchange
so a future forex module can reuse the same structure.
"""

import datetime as dt
import logging
import time
from collections.abc import Iterable, Mapping
from typing import Any, Callable

import pandas as pd
import pyotp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from . import settings
from .symbols import MCX_COMMODITIES, resolve_active_token

logger = logging.getLogger(__name__)

# Shoonya candle strings arrive as e.g. "24-Jan-2025 09:15:00" (IST).
CANDLE_TIME_FORMAT = "%d-%b-%Y %H:%M:%S"


class ShoonyaAuthError(RuntimeError):
    """Login failed or the session token expired irrecoverably."""


class ShoonyaGatewayError(RuntimeError):
    """Broker gateway unreachable or returned non-JSON (HTTP 5xx / maintenance).

    Retryable: distinct from credential rejection, which is fatal.
    """


class ShoonyaHistoryError(RuntimeError):
    """A TPSeries request failed at the transport or protocol level."""


def _load_noren_api() -> Callable[..., Any]:
    try:
        from NorenRestApiPy.NorenApi import NorenApi
    except ImportError as exc:
        raise RuntimeError(
            "NorenRestApiPy is not installed. Run `pip install -r requirements.txt`."
        ) from exc
    return NorenApi


def _load_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("DuckDB support is not installed. Run `pip install -r requirements.txt`.") from exc
    return duckdb


def _to_ist_date(value: str | dt.date) -> dt.date:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    return dt.date.fromisoformat(str(value))


def _day_start(date: dt.date) -> dt.datetime:
    return dt.datetime.combine(date, dt.time.min, tzinfo=settings.IST)


def _day_end(date: dt.date) -> dt.datetime:
    return dt.datetime.combine(date, dt.time.max, tzinfo=settings.IST)


class MCXIngestionEngine:
    """Throttled, crash-tolerant downloader for Shoonya MCX minute candles."""

    def __init__(self, api: Any = None, duckdb_module: Any = None) -> None:
        self._duckdb = duckdb_module or _load_duckdb()
        noren_api = api or _load_noren_api()(
            host=settings.SHOONYA_HOST,
            websocket=settings.SHOONYA_WS_URL,
        )
        self.api = noren_api
        self._token_cache: dict[str, str] = {}
        self._authenticated = False
        self._init_duckdb()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def _login_once(self) -> Mapping[str, Any] | None:
        """Single QuickAuth attempt with a freshly generated TOTP code.

        The Noren client parses the response with ``json.loads`` before
        checking status, so a gateway 5xx (HTML error page) surfaces as a
        JSON decode error -- translate those into a retryable gateway error.
        """
        otp = pyotp.TOTP(settings.SHOONYA_TOTP_KEY).now()
        try:
            return self.api.login(
                userid=settings.SHOONYA_USER_ID,
                password=settings.SHOONYA_PASSWORD,
                twoFA=otp,
                vendor_code=settings.SHOONYA_VENDOR_CODE,
                api_secret=settings.SHOONYA_API_SECRET,
                imei=settings.SHOONYA_IMEI,
            )
        except (ValueError, OSError) as exc:
            # ValueError: JSONDecodeError on non-JSON body. OSError: requests
            # connection/timeout errors (RequestException subclasses IOError).
            raise ShoonyaGatewayError(
                f"Shoonya gateway returned a non-JSON response or was unreachable "
                f"(likely HTTP 5xx / maintenance): {exc}"
            ) from exc

    @retry(
        stop=stop_after_attempt(settings.MAX_RETRIES),
        wait=wait_exponential(multiplier=10, min=10, max=300),
        retry=retry_if_exception_type(ShoonyaGatewayError),
        reraise=True,
    )
    def login(self) -> None:
        """Authenticate against Shoonya, retrying transient gateway failures."""
        settings.validate_settings()
        response = self._login_once()
        if not response or response.get("stat") != "Ok":
            raise ShoonyaAuthError("Shoonya login rejected (check credentials / TOTP clock skew).")
        self._authenticated = True
        logger.info("Authenticated with Shoonya as %s", settings.SHOONYA_USER_ID)

    def _session_alive(self) -> bool:
        """Cheap liveness probe: Limits succeeds only with a valid token."""
        try:
            return self.api.get_limits() is not None
        except Exception:
            return False

    def _ensure_session(self) -> None:
        """Re-authenticate transparently when the daily token has expired."""
        if not self._authenticated:
            self.login()
            return
        if not self._session_alive():
            logger.warning("Shoonya session token expired; re-authenticating...")
            self.login()

    # ------------------------------------------------------------------
    # DuckDB schema
    # ------------------------------------------------------------------
    def _init_duckdb(self) -> None:
        settings.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        con = self._duckdb.connect(str(settings.DB_PATH))
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS commodity_prices (
                    symbol VARCHAR NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    volume BIGINT,
                    PRIMARY KEY (symbol, timestamp)
                );
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS ingestion_meta (
                    symbol VARCHAR PRIMARY KEY,
                    oldest_date DATE,
                    last_completed_end TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
        finally:
            con.close()

    def _meta_get(self, symbol: str, column: str) -> Any:
        con = self._duckdb.connect(str(settings.DB_PATH))
        try:
            row = con.execute(
                f"SELECT {column} FROM ingestion_meta WHERE symbol = ?", (symbol,)
            ).fetchone()
        finally:
            con.close()
        return row[0] if row else None

    def _meta_set(self, symbol: str, column: str, value: Any) -> None:
        con = self._duckdb.connect(str(settings.DB_PATH))
        try:
            # updated_at is bound as a parameter: DuckDB misparses bare
            # CURRENT_TIMESTAMP inside ON CONFLICT DO UPDATE as a column name.
            con.execute(
                f"""
                INSERT INTO ingestion_meta (symbol, {column}, updated_at) VALUES (?, ?, ?)
                ON CONFLICT (symbol) DO UPDATE SET
                    {column} = excluded.{column},
                    updated_at = excluded.updated_at
                """,
                (symbol, value, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)),
            )
        finally:
            con.close()

    # ------------------------------------------------------------------
    # Low-level TPSeries access
    # ------------------------------------------------------------------
    @retry(
        stop=stop_after_attempt(settings.MAX_RETRIES),
        wait=wait_exponential(multiplier=5, min=5, max=120),
        retry=retry_if_exception_type(
            (ShoonyaHistoryError, ShoonyaGatewayError, ConnectionError, TimeoutError)
        ),
        reraise=True,
    )
    def _tps(
        self,
        exchange: str,
        token: str,
        start: dt.datetime,
        end: dt.datetime,
        check_session: bool = False,
    ) -> list[dict]:
        """One TPSeries call. Returns candle dicts; [] means 'no data here'.

        ``check_session`` distinguishes a dead token from an empty window by
        probing Limits before giving up -- used during downloads where data
        is expected, skipped during boundary probes to halve request volume.
        """
        try:
            rows = self.api.get_time_price_series(
                exchange=exchange,
                token=token,
                starttime=start.timestamp(),
                endtime=end.timestamp(),
                interval=settings.INTERVAL,
            )
        except Exception as exc:
            raise ShoonyaHistoryError(f"TPSeries request failed: {exc}") from exc

        if isinstance(rows, list):
            return rows

        # None => the API answered with an error document.
        if check_session and not self._session_alive():
            logger.warning("Shoonya session expired mid-download; re-authenticating and retrying.")
            self.login()
            rows = self.api.get_time_price_series(
                exchange=exchange,
                token=token,
                starttime=start.timestamp(),
                endtime=end.timestamp(),
                interval=settings.INTERVAL,
            )
            if isinstance(rows, list):
                return rows
        return []

    @staticmethod
    def _rows_to_frame(rows: list[dict], symbol: str) -> pd.DataFrame:
        """Normalise raw TPSeries dicts into the strict DuckDB schema."""
        if not rows:
            return pd.DataFrame(
                columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"]
            )
        frame = pd.DataFrame(rows)
        frame = frame.rename(
            columns={"ot": "open", "hi": "high", "lo": "low", "cl": "close", "vol": "volume"}
        )
        frame["timestamp"] = pd.to_datetime(frame["time"], format=CANDLE_TIME_FORMAT, errors="coerce")
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["timestamp", "close"])
        frame["volume"] = frame["volume"].fillna(0).clip(lower=0).astype("int64")
        frame["symbol"] = symbol
        return frame[["symbol", "timestamp", "open", "high", "low", "close", "volume"]]

    def _probe(self, exchange: str, token: str, start: dt.date, end: dt.date) -> bool:
        """True when TPSeries serves at least one candle inside [start, end]."""
        rows = self._tps(exchange, token, _day_start(start), _day_end(end))
        return bool(rows)

    # ------------------------------------------------------------------
    # Binary search engine: historical boundary discovery
    # ------------------------------------------------------------------
    def find_oldest_available_date(
        self, commodity: str, exchange: str, token: str
    ) -> dt.date | None:
        """Binary-search the earliest date with minute data, then refine.

        The predicate "window [d, d+W) contains data" is monotone in d, so a
        classic bisection between SEARCH_START and today converges on the
        structural history boundary; a short forward walk then pins the exact
        oldest day (the bisection alone lands up to W-1 days early).
        """
        print(f"Discovering historical boundary for {commodity}...")
        today = dt.datetime.now(settings.IST).date()
        if not self._probe(exchange, token, today - dt.timedelta(days=settings.PROBE_WINDOW_DAYS), today):
            logger.error("No recent data for %s; boundary search aborted", commodity)
            return None

        lo, hi = settings.SEARCH_START, today
        while lo < hi:
            mid = lo + (hi - lo) // 2
            if self._probe(exchange, token, mid, mid + dt.timedelta(days=settings.PROBE_WINDOW_DAYS - 1)):
                hi = mid  # data exists at midpoint -> look earlier
            else:
                lo = mid + dt.timedelta(days=1)  # empty/error -> look later
            time.sleep(settings.PROBE_DELAY_SECONDS)

        # Refine to the exact first day that carries candles.
        oldest = lo
        for _ in range(settings.PROBE_WINDOW_DAYS):
            if oldest > today:
                break
            if self._probe(exchange, token, oldest, oldest):
                break
            oldest += dt.timedelta(days=1)
            time.sleep(settings.PROBE_DELAY_SECONDS)

        if oldest > today:
            return None
        print(f"Oldest data found for {commodity}: {oldest.isoformat()}")
        return oldest

    # ------------------------------------------------------------------
    # Chunked downloading
    # ------------------------------------------------------------------
    @staticmethod
    def generate_chunks(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
        """Split [start, end] into forward CHUNK_SIZE_DAYS windows."""
        chunks: list[tuple[dt.date, dt.date]] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + dt.timedelta(days=settings.CHUNK_SIZE_DAYS - 1), end)
            chunks.append((cursor, chunk_end))
            cursor = chunk_end + dt.timedelta(days=1)
        return chunks

    def _fetch_window(
        self, exchange: str, token: str, symbol: str, start: dt.date, end: dt.date
    ) -> pd.DataFrame:
        """Fetch one chunk, bisecting whenever the response hits the cap.

        TPSeries silently truncates at ~1000 candles; a saturated response
        is therefore split in half and re-requested until each sub-window
        returns fewer rows than the cap (or is already a single day).
        """
        rows = self._tps(exchange, token, _day_start(start), _day_end(end), check_session=True)
        frame = self._rows_to_frame(rows, symbol)
        if len(frame) >= settings.MAX_CANDLES_PER_REQUEST and (end - start) >= dt.timedelta(days=1):
            mid = start + (end - start) / 2  # date arithmetic yields a date
            left = self._fetch_window(exchange, token, symbol, start, mid)
            time.sleep(settings.DELAY_SECONDS)
            right = self._fetch_window(exchange, token, symbol, mid + dt.timedelta(days=1), end)
            frame = pd.concat([left, right], ignore_index=True)
        return frame

    def _upsert(self, frame: pd.DataFrame) -> int:
        """Idempotently merge a candle frame into commodity_prices."""
        if frame.empty:
            return 0
        frame = frame.drop_duplicates(subset=["symbol", "timestamp"], keep="last")
        con = self._duckdb.connect(str(settings.DB_PATH))
        try:
            con.register("frame", frame)
            con.execute(
                """
                INSERT INTO commodity_prices BY NAME
                SELECT symbol, timestamp, open, high, low, close, volume FROM frame
                ON CONFLICT (symbol, timestamp) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume
                """
            )
        finally:
            con.close()
        return len(frame)

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------
    def _token_for(self, commodity: str) -> tuple[str, str]:
        """Resolve (and memoise) the active token for a commodity."""
        config = MCX_COMMODITIES[commodity]
        if commodity not in self._token_cache:
            self._ensure_session()
            self._token_cache[commodity] = resolve_active_token(self.api, commodity, config)
        return str(config["exchange"]), self._token_cache[commodity]

    def ingest_commodity(
        self,
        commodity: str,
        start_date: str | dt.date | None = None,
        end_date: str | dt.date | None = None,
        refresh_boundary: bool = False,
    ) -> int:
        """Download the full (or resumed) 1-minute history for one commodity."""
        if commodity not in MCX_COMMODITIES:
            raise KeyError(f"Unknown MCX commodity '{commodity}'")
        exchange, token = self._token_for(commodity)
        end = _to_ist_date(end_date) if end_date else dt.datetime.now(settings.IST).date()

        if start_date:
            start = _to_ist_date(start_date)
        else:
            cached = None if refresh_boundary else self._meta_get(commodity, "oldest_date")
            if cached is not None:
                start = cached if isinstance(cached, dt.date) else _to_ist_date(str(cached))
                print(f"Oldest data found for {commodity}: {start.isoformat()} (cached)")
            else:
                start = self.find_oldest_available_date(commodity, exchange, token)
                if start is None:
                    logger.error("No historical boundary discovered for %s; skipping", commodity)
                    return 0
                self._meta_set(commodity, "oldest_date", start)

            # Incremental resume: continue from the last completed chunk.
            last_end = self._meta_get(commodity, "last_completed_end")
            if last_end is not None:
                resume_from = last_end.date() + dt.timedelta(days=1 - settings.RESUME_OVERLAP_DAYS)
                if resume_from > start:
                    logger.info("Resuming %s from %s", commodity, resume_from.isoformat())
                    start = resume_from

        total_rows = 0
        chunks = self.generate_chunks(start, end)
        for number, (chunk_start, chunk_end) in enumerate(chunks, start=1):
            print(f"Downloading 1-min chunk: {chunk_start.isoformat()} to {chunk_end.isoformat()}...")
            try:
                frame = self._fetch_window(exchange, token, commodity, chunk_start, chunk_end)
                rows = self._upsert(frame)
            except (ShoonyaHistoryError, ShoonyaGatewayError, ShoonyaAuthError) as exc:
                logger.error(
                    "[%s chunk %d/%d failed: %s -- rerunning will resume here]",
                    commodity, number, len(chunks), exc,
                )
                break
            total_rows += rows
            print(f"Successfully upserted {rows} rows into DuckDB for {commodity}")
            self._meta_set(commodity, "last_completed_end", _day_end(chunk_end))
            time.sleep(settings.DELAY_SECONDS)

        logger.info("Completed %s: %d rows across %d chunks", commodity, total_rows, len(chunks))
        return total_rows

    def run(
        self,
        commodities: Iterable[str] | None = None,
        start_date: str | dt.date | None = None,
        end_date: str | dt.date | None = None,
        refresh_boundary: bool = False,
    ) -> int:
        """Authenticate once, then ingest each commodity sequentially."""
        commodities = list(commodities or MCX_COMMODITIES)
        self.login()
        grand_total = 0
        failures: list[str] = []
        for commodity in commodities:
            try:
                grand_total += self.ingest_commodity(
                    commodity, start_date=start_date, end_date=end_date,
                    refresh_boundary=refresh_boundary,
                )
            except (ShoonyaAuthError, ShoonyaGatewayError, ShoonyaHistoryError, KeyError, TimeoutError) as exc:
                failures.append(commodity)
                logger.error("Ingestion failed for %s: %s", commodity, exc)
        if failures:
            logger.error("Failed commodities: %s", ", ".join(failures))
        logger.info("Run complete: %d total rows upserted", grand_total)
        return grand_total

    def stats(self) -> pd.DataFrame:
        """Per-symbol coverage summary for quick health checks."""
        con = self._duckdb.connect(str(settings.DB_PATH))
        try:
            return con.execute(
                """
                SELECT symbol,
                       COUNT(*) AS rows,
                       MIN(timestamp) AS first_candle,
                       MAX(timestamp) AS last_candle
                FROM commodity_prices
                GROUP BY symbol
                ORDER BY symbol
                """
            ).df()
        finally:
            con.close()
