# OpenAlgo Candle Visualizer

Self-contained local web UI for browsing historical candle stores (any `.duckdb`
file under `market_data/`) across all timeframes.

## Run

```bash
python visualizer/server.py
```

Opens `http://127.0.0.1:8501` in your browser automatically (Ctrl+C to stop).

```bash
python visualizer/server.py --port 9000        # custom port
python visualizer/server.py --no-browser       # headless
python visualizer/server.py --db /path/to/other/backtest_data.duckdb
OPENALGO_DB_PATH=/path/to/db python visualizer/server.py   # env override
```

Requires only the project venv (`duckdb`); no extra packages. The TradingView
lightweight-charts library is vendored under `vendor/`, so it works offline.

## Features

- **DuckDB file picker**: every `.duckdb` file under `market_data/` is discovered
  automatically (including subfolders) and selectable in the toolbar — e.g. the
  openalgo equity store and the shoonya MCX store
- **Schema-agnostic tables**: candle tables are detected by their columns
  (`timestamp/open/high/low/close`), so non-`ohlcv_*` stores like the MCX
  `commodity_prices` table work without server changes; the timeframe control
  is hidden entirely when a database has only one table
- Candlestick + volume chart with pan/zoom/crosshair; the whole selected range
  loads in one request (capped at the most recent 100k candles)
- **IST-enforced axis**: tick marks, crosshair label and CSV timestamps are
  always Asia/Kolkata regardless of the machine timezone
- Timeframe/table switch (reads every candle table in the selected DB)
- Symbol type-ahead from live DB contents (custom symbols allowed)
- Date-range picker + quick chips (1D/1W/1M/3M/6M/1Y/ALL) anchored on data coverage
- Overlays: SMA20 / SMA50 / EMA9 / session-anchored intraday VWAP (auto-disabled on 1d) / volume MA-20
- Crosshair legend with O/H/L/C/V and % change vs previous close
- Log-scale price axis toggle, chart watermark (`SYMBOL · TF`)
- CSV export of the current page (IST timestamps)
- Stale-aggregate banner when an `ohlcv_*` aggregate table lags `ohlcv_1m` by >12h
- Preferences persisted in localStorage; read-only DB access safe alongside downloads

## API

| Endpoint | Description |
| --- | --- |
| `GET /api/databases` | List `.duckdb` files found under `market_data/` |
| `GET /api/tables?db=` | Candle tables in a DB (name discovered by schema) |
| `GET /api/symbols?db=&table=` | Distinct symbols in a table |
| `GET /api/meta?db=&table=` | Row count, symbol count, date coverage |
| `GET /api/candles?db=&table=&symbol=&start=&end=&limit=` | Candles for a symbol (most recent `limit`, max 100k) |
| `GET /api/freshness?db=` | Per-table row count and latest timestamp |
| `GET /api/strategies`, `GET /api/system/stats`, `POST /api/backtest/run` | Backtest dashboard endpoints (use the default DB) |

`--db` / `OPENALGO_DB_PATH` still set the server default; the `db` query param
selects any file inside `market_data/` per request (paths outside it are rejected).
