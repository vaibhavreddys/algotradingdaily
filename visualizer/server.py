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
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from strategies.registry import discover_strategies, load_strategy_instance
from backtesting.portfolio_sim import scan_universe_signals, simulate_portfolio_execution
from data_pipeline import get_available_symbols, fetch_nifty_benchmark
from config import CONFIG, TradingConfig


def q_strategies(_con, _params) -> dict:
    """Return all discovered strategies and their version modules."""
    return {"strategies": discover_strategies()}


def q_system_stats(con, _params) -> dict:
    """Return DuckDB telemetry and system status."""
    db_size_mb = 0
    if DEFAULT_DB.exists():
        db_size_mb = round(DEFAULT_DB.stat().st_size / (1024 * 1024), 2)
    
    total_candles = 0
    total_symbols = 0
    min_ts = ""
    max_ts = ""
    
    try:
        row = con.execute("SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(timestamp), MAX(timestamp) FROM ohlcv_1m").fetchone()
        if row:
            total_candles = row[0] or 0
            total_symbols = row[1] or 0
            min_ts = str(row[2])[:10] if row[2] else ""
            max_ts = str(row[3])[:10] if row[3] else ""
    except Exception:
        pass

    return {
        "db_path": str(DEFAULT_DB),
        "db_size_mb": db_size_mb,
        "total_candles": total_candles,
        "total_symbols": total_symbols,
        "first_date": min_ts,
        "last_date": max_ts,
        "strategies_count": len(discover_strategies()),
    }


def run_backtest_api(payload: dict) -> dict:
    """Run backtest simulation and return structured KPI and trade log results."""
    strategy_id = payload.get("strategy_id", "vwap_stoch_breakdown")
    version_mod = payload.get("version", "v1_0")
    universe = payload.get("universe", "NIFTY50")
    capital = float(payload.get("capital", 10000.0))

    strategy = load_strategy_instance(strategy_id, version_mod)
    if not strategy:
        raise ApiError(400, f"Strategy {strategy_id}:{version_mod} not found.")

    symbols = get_available_symbols(universe=universe)
    bench_map = fetch_nifty_benchmark(interval=strategy.TIMEFRAME, universe=universe)
    
    # Custom config instance with requested capital
    custom_cfg = TradingConfig(
        INITIAL_CAPITAL=capital,
        MAX_CONCURRENT_POSITIONS=CONFIG.MAX_CONCURRENT_POSITIONS,
        MAX_RISK_PER_TRADE_PCT=CONFIG.MAX_RISK_PER_TRADE_PCT,
        MAX_DAILY_LOSS_PCT=CONFIG.MAX_DAILY_LOSS_PCT
    )

    signals_df = scan_universe_signals(symbols, bench_map, config=custom_cfg)
    tdf, ending_capital, total_charges, _, _ = simulate_portfolio_execution(
        signals_df=signals_df,
        config=custom_cfg
    )

    if tdf is None or tdf.empty:
        return {
            "strategy": strategy.NAME,
            "version": strategy.VERSION,
            "universe": universe,
            "trades_count": 0,
            "initial_capital": capital,
            "ending_capital": capital,
            "net_pnl": 0.0,
            "net_roi_pct": 0.0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            "equity_curve": [],
            "trades": [],
            "broker_comparison": []
        }

    # Calculate Core Performance Metrics
    total_trades = len(tdf)
    winning_trades = int((tdf['Gross PnL (₹)'] > 0).sum())
    win_rate = round((winning_trades / total_trades) * 100, 2)
    gross_pnl = float(tdf['Gross PnL (₹)'].sum())
    net_pnl = float(tdf['Net PnL (₹)'].sum())
    net_roi = round((net_pnl / capital) * 100, 2)
    
    gross_wins = tdf.loc[tdf['Gross PnL (₹)'] > 0, 'Gross PnL (₹)'].sum()
    gross_losses = abs(tdf.loc[tdf['Gross PnL (₹)'] < 0, 'Gross PnL (₹)'].sum())
    profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else 999.0

    # Equity curve & Max Drawdown
    equity_series = tdf['Capital'].tolist()
    peak = capital
    max_dd = 0.0
    max_dd_pct = 0.0
    equity_curve = [{"time": "Start", "equity": capital}]
    
    for idx, row in tdf.iterrows():
        cap = float(row['Capital'])
        if cap > peak:
            peak = cap
        dd = peak - cap
        dd_pct = (dd / peak) * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd_pct
        equity_curve.append({
            "time": str(row['Exit Time']),
            "symbol": row['Symbol'],
            "equity": round(cap, 2),
            "pnl": round(float(row['Net PnL (₹)']), 2)
        })

    # Multi-broker breakdown
    from core.charges import calculate_charges, BROKER_CHARGES_CONFIG
    
    broker_comparison = []
    for b_key, b_cfg in BROKER_CHARGES_CONFIG.items():
        b_name = b_cfg.get("name", b_key)
        b_fees = 0.0
        for _, r in tdf.iterrows():
            exposure = float(r.get('Exposure (₹)', 0.0))
            pnl_val = float(r.get('Gross PnL (₹)', 0.0))
            sell_turn = exposure
            buy_turn = max(0.0, exposure - pnl_val)
            fee = calculate_charges(sell_turnover=sell_turn, buy_turnover=buy_turn, broker=b_key)
            b_fees += fee
            
        b_net = gross_pnl - b_fees
        broker_comparison.append({
            "broker": b_name,
            "total_fees": round(b_fees, 2),
            "net_pnl": round(b_net, 2),
            "roi_pct": round((b_net / capital) * 100, 2)
        })

    # Convert trades table
    trades_log = []
    for _, r in tdf.tail(100).iterrows():
        trades_log.append({
            "symbol": r['Symbol'],
            "entry_time": str(r['Entry Time']),
            "exit_time": str(r['Exit Time']),
            "result": r['Result'],
            "gross_pnl": round(float(r['Gross PnL (₹)']), 2),
            "net_pnl": round(float(r['Net PnL (₹)']), 2),
            "pnl_pct": round(float(r['PnL %']), 2),
            "ending_capital": round(float(r['Capital']), 2)
        })

    return {
        "strategy": strategy.NAME,
        "version": strategy.VERSION,
        "universe": universe,
        "trades_count": total_trades,
        "initial_capital": capital,
        "ending_capital": round(ending_capital, 2),
        "gross_pnl": round(gross_pnl, 2),
        "total_taxes_fees": round(gross_pnl - net_pnl, 2),
        "net_pnl": round(net_pnl, 2),
        "net_roi_pct": net_roi,
        "win_rate_pct": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "equity_curve": equity_curve,
        "trades": trades_log,
        "broker_comparison": broker_comparison
    }


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
    "/api/strategies": q_strategies,
    "/api/system/stats": q_system_stats,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenAlgoViz/1.0"
    db_path: Path = DEFAULT_DB

    def log_message(self, fmt, *args):
        pass

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/backtest/run":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body) if body else {}
            try:
                data = run_backtest_api(payload)
                self._send_json(data)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        self._send_json({"error": "Endpoint not found"}, 404)

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
            if parsed.path == "/" or parsed.path == "/index.html" or parsed.path == "/dashboard":
                self._serve_file(APP_DIR / "dashboard.html", "text/html; charset=utf-8")
                return
            if parsed.path == "/chart" or parsed.path == "/chart.html" or parsed.path == "/app.html":
                self._serve_file(APP_DIR / "app.html", "text/html; charset=utf-8")
                return
            if parsed.path == "/backtest" or parsed.path == "/backtest.html":
                self._serve_file(APP_DIR / "backtest.html", "text/html; charset=utf-8")
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
