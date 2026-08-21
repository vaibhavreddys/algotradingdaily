# OpenAlgo Candle Visualizer

Self-contained local web UI for browsing the OpenAlgo historical candle store
(`market_data/openalgo/backtest_data.duckdb`) across all timeframes.

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

- Candlestick + volume chart with pan/zoom/crosshair
- **IST-enforced axis**: tick marks, crosshair label and CSV timestamps are
  always Asia/Kolkata regardless of the machine timezone
- Timeframe switch: 1m / 5m / 15m / 1h / 1d (reads `ohlcv_*` tables)
- Symbol type-ahead from live DB contents (custom symbols allowed)
- Date-range picker + quick chips (1D/1W/1M/3M/6M/1Y/ALL) anchored on data coverage
- Overlays: SMA20 / SMA50 / EMA9 / session-anchored intraday VWAP (auto-disabled on 1d) / volume MA-20
- Crosshair legend with O/H/L/C/V and % change vs previous close
- Log-scale price axis toggle, chart watermark (`SYMBOL · TF`)
- CSV export of the currently loaded view (IST timestamps)
- Stale-aggregate banner when an aggregate table lags `ohlcv_1m` by >12h
- Preferences persisted in localStorage; read-only DB access safe alongside downloads
