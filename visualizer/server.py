"""Local HTTP visualizer for the OpenAlgo DuckDB candle store.

Zero-dependency server (stdlib + duckdb) serving a single-page chart UI.
Run: python visualizer/server.py [--port 8501] [--no-browser]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path(__file__).resolve().parent
DEFAULT_DB = REPO_ROOT / "market_data" / "openalgo" / "backtest_data.duckdb"
TABLES = ("ohlcv_1m", "ohlcv_5m", "ohlcv_15m", "ohlcv_1h", "ohlcv_1d")
MAX_LIMIT = 100_000
SYMBOL_RE = re.compile(r"^[A-Z0-9&_.\-]{1,30}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_db_path(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    import os

    env = os.getenv("OPENALGO_DB_PATH")
    if env:
        return Path(env).expanduser().resolve()
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from data_pipeline.openalgo_ingestion import settings

        return Path(settings.DB_PATH)
    except Exception:
        return DEFAULT_DB


def ist_day_bound(day: str, end: bool = False):
    base = dt.date.fromisoformat(day)
    offset = dt.timedelta(days=1 if end else 0)
    naive = f"{base + offset} 00:00:00"
    return f"(CAST('{naive}' AS TIMESTAMP) AT TIME ZONE 'Asia/Kolkata')"


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def db_connect(db_path: Path):
    if not db_path.exists():
        raise ApiError(503, f"Database not found at {db_path}. Run the ingestion first.")
    try:
        return duckdb.connect(str(db_path), read_only=True)
    except Exception as exc:
        raise ApiError(503, f"Cannot open database: {exc}")


def validate_table(table: str) -> str:
    if table not in TABLES:
        raise ApiError(400, f"Unknown timeframe table '{table}'. Allowed: {list(TABLES)}")
    return table


def validate_symbol(symbol: str) -> str:
    clean = symbol.strip().upper()
    if not SYMBOL_RE.match(clean):
        raise ApiError(400, "Invalid symbol format.")
    return clean


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ApiError(400, "Dates must be YYYY-MM-DD.")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ApiError(400, f"Invalid date: {value}") from exc
    return value


def q_symbols(con, _params) -> dict:
    rows = con.execute("SELECT DISTINCT symbol FROM ohlcv_1m ORDER BY symbol").fetchall()
    return {"symbols": [r[0] for r in rows]}


def q_meta(con, params) -> dict:
    table = validate_table((params.get("table") or ["ohlcv_1m"])[0])
    try:
        row = con.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(timestamp), MAX(timestamp) FROM {table}"
        ).fetchone()
    except duckdb.CatalogException:
        raise ApiError(
            400,
            f"Table {table} does not exist yet. Build it with: "
            "python openalgo_ingest.py --action aggregate",
        )
    return {
        "table": table,
        "rows": int(row[0]),
        "symbols": int(row[1]),
        "min_ts": int(row[2].timestamp()) if row[2] else None,
        "max_ts": int(row[3].timestamp()) if row[3] else None,
    }


def q_candles(con, params) -> dict:
    symbol = validate_symbol(params.get("symbol", [""])[0])
    table = validate_table((params.get("table") or ["ohlcv_1m"])[0])
    start = validate_date(params["start"][0]) if params.get("start") else None
    end = validate_date(params["end"][0]) if params.get("end") else None
    limit = min(int(params.get("limit", [MAX_LIMIT])[0]), MAX_LIMIT)

    where = ["symbol = ?"]
    args: list = [symbol]
    if start:
        where.append(f"timestamp >= {ist_day_bound(start)}")
    if end:
        where.append(f"timestamp < {ist_day_bound(end, end=True)}")
    query = (
        f"SELECT epoch(timestamp), open, high, low, close, volume FROM {table} "
        f"WHERE {' AND '.join(where)} ORDER BY timestamp ASC LIMIT {limit + 1}"
    )
    try:
        rows = con.execute(query, args).fetchall()
    except duckdb.CatalogException:
        raise ApiError(
            400,
            f"Table {table} does not exist yet. Build it with: "
            "python openalgo_ingest.py --action aggregate",
        )
    truncated = len(rows) > limit
    candles = [[int(r[0]), *map(float, r[1:5]), int(r[5])] for r in rows[:limit]]
    return {
        "symbol": symbol,
        "table": table,
        "count": len(candles),
        "truncated": truncated,
        "candles": candles,
    }


def q_freshness(con, _params) -> dict:
    out = {}
    for table in TABLES:
        try:
            row = con.execute(f"SELECT COUNT(*), MAX(timestamp) FROM {table}").fetchone()
            out[table] = {
                "rows": int(row[0]),
                "max_ts": int(row[1].timestamp()) if row[1] else None,
            }
        except duckdb.CatalogException:
            out[table] = {"rows": 0, "max_ts": None}
    return out


ROUTES = {
    "/api/symbols": q_symbols,
    "/api/meta": q_meta,
    "/api/candles": q_candles,
    "/api/freshness": q_freshness,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenAlgoViz/1.0"
    db_path: Path = DEFAULT_DB

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self._send_json({"error": f"Missing file: {path.name}"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = ROUTES.get(parsed.path)
        try:
            if parsed.path == "/" or parsed.path == "/index.html":
                self._serve_file(APP_DIR / "app.html", "text/html; charset=utf-8")
                return
            if parsed.path.startswith("/vendor/"):
                requested = (parsed.path[len("/vendor/"):]).lstrip("/.")
                self._serve_file(APP_DIR / "vendor" / requested, "application/javascript")
                return
            if route is None:
                self._send_json({"error": "Not found"}, 404)
                return
            con = db_connect(self.db_path)
            try:
                self._send_json(route(con, parse_qs(parsed.query)))
            finally:
                con.close()
        except ApiError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except (duckdb.Error, ValueError, KeyError) as exc:
            self._send_json({"error": f"Bad request: {exc}"}, 400)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenAlgo candle-store visualizer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--db", help="Override path to backtest_data.duckdb")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    Handler.db_path = resolve_db_path(args.db)

    with db_connect(Handler.db_path):
        pass

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    url = f"http://{args.host}:{args.port}/"
    print(f"[viz] serving {Handler.db_path}")
    print(f"[viz] open {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[viz] stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
