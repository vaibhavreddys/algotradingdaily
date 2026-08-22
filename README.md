# Quantitative Algorithmic Trading & Portfolio Simulation Engine

A high-performance, modular algorithmic trading platform and portfolio simulation suite for Indian equity markets (NSE).

Built with institutional-grade risk management, sub-second execution daemons, dynamic compounding, and multi-broker statutory fee modeling.

---

## 🌟 Key Architecture & Capabilities

* **Multi-Asset Execution Daemons**: High-frequency position guardian, automated scheduled scans on candle close, and resilient retry gateways.
* **Institutional Risk Engine**:
  * **Dual-Guard Position Sizing**: Capital risk budgeting coupled with margin allocation.
  * **Daily Circuit Breakers**: Automatic intraday portfolio protection.
  * **Dynamic Trailing Stop Loss**: Automated breakeven trailing.
  * **Mandatory Day-End Squareoff**: Enforces strict intraday discipline.
* **Multi-Broker Friction Modeling**: Precise calculations for STT, NSE transaction charges, SEBI turnover fees, Stamp Duty, GST, and broker commission schedules.
* **Persistent Ledger Architecture**: SQLite WAL-mode state machine with automated crash recovery, pre-market reconciliation, and atomic balance tracking.
* **Cross-Platform Power Management**: Automated OS sleep inhibitor preventing system suspension during active trading sessions.
* **Multi-Channel Alerts**: Modular alert dispatching framework supporting Telegram and custom channels.

---

## 📊 Backtest Analytics (59-Day Simulation)

Simulation benchmarked across **59 Trading Days (2026-06-01 to 2026-08-21)** with **₹10,000 Starting Capital**, dynamic compounding, and regulatory fee models:

| Performance Metric | Quantitative Value |
| :--- | :--- |
| **Initial Capital** | ₹10,000.00 |
| **Max Concurrent Positions** | 2 Simultaneous Slots (Equal Split) |
| **Total Trades Taken** | 122 Trades (56 Wins / 66 Losses) |
| **Win Rate** | **45.90%** |
| **Gross Realized Profit** | **+₹4,580.69 (+45.81%)** |
| **Total Statutory Taxes & Fees** | **₹2,660.65** |
| **Net Realized Profit** | **+₹1,920.04 (+19.20% Net Return)** *(Post-All Charges)* |
| **Ending Capital Balance** | **₹11,920.04** |
| **Profit Factor** | **1.51** |
| **Max Drawdown (MDD)** | **₹1,429.20 (-11.81%)** |
| **Max Equity Runup** | **+₹3,439.18 (+34.39%)** *(Trough-to-Peak Surge)* |
| **Max Consecutive Streaks** | **5 Wins in a row** / **9 Losses in a row** |
| **Largest Single Trade** | **+₹1,104.28 Win** / **-₹772.39 Loss** |
| **Trade Expectancy** | **+₹15.74 / trade** |
| **Avg Win / Avg Loss** | **+₹241.72 / -₹135.69** |

> 📚 **Detailed Backtest Studies & Broker Matrix**: See [`docs/backtest_results_and_broker_matrix.md`](docs/backtest_results_and_broker_matrix.md)

---

## 🏗️ Repository Structure

```text
algotradingdaily/
├── config.py              # Centralized TradingConfig dataclass & risk parameters
│
├── core/                  # Core analytics, risk controls, fees & persistence
│   ├── risk.py            # Stop/target calculations, dual-guard sizing & circuit breaker
│   ├── capital.py         # Slot margin calculations & persistent balances
│   ├── trade_db.py        # Isolated SQLite trade journals (WAL mode & atomic balance ledger)
│   ├── indicators.py      # Quantitative technical formulas
│   └── charges.py         # Universal Indian taxes & multi-broker fee engine
│
├── strategies/            # Modular strategy definitions & signal extractors
│
├── data_pipeline/         # Market data gateway & high-frequency tick caching
│   ├── data_feed.py       # Smart local caching, silent scans & live tick fetcher
│   └── openalgo_ingestion/# Isolated OpenAlgo 1m downloader and DuckDB reader
│
├── visualizer/            # Self-contained candle chart UI (localhost:8501)

│
├── backtesting/           # Historical simulation & scanning engines
│   ├── portfolio_sim.py   # Chronological multi-stock portfolio simulator with compounding
│   └── scanner.py         # Unconstrained multi-stock indicator scanner
│
├── live_trading/          # Execution engines & schedulers
│   ├── base_engine.py     # Base engine with sleep inhibitor, funnel renderer & reconciler
│   ├── paper_trader.py    # High-frequency guardian virtual paper trading daemon
│   └── live_trader.py     # Real-money OMS order placement engine
│
├── alerts/                # Multi-channel notification framework
│   ├── base.py            # BaseAlertChannel interface & dynamic channel dispatcher
│   └── telegram.py        # Telegram bot push notifications
│
├── docs/                  # Architectural & operational documentation
│   ├── strategy_specification.md
│   ├── backtest_results_and_broker_matrix.md
│   ├── broker_configuration_guide.md
│   └── cloud_execution_setup_guide.md
│
└── tests/                 # Comprehensive unit test suite (48 tests)
```

---

## 🚀 Setup & Quickstart

### 1. Clone & Setup Environment:
```bash
git clone https://github.com/vaibhavreddys/algotradingdaily.git
cd algotradingdaily

# Setup Python Virtual Environment
python -m venv venv
.\venv\Scripts\activate      # Windows
# source venv/bin/activate   # Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment:
```bash
cp .env.example .env
```
> 📖 For detailed broker setup, API key generation, and credentials configuration, refer to [`docs/broker_configuration_guide.md`](docs/broker_configuration_guide.md).

---

## ⚡ Usage

### Run Portfolio Simulation:
```bash
python backtesting/portfolio_sim.py
```

### Download OpenAlgo historical 1-minute candles

The optional OpenAlgo ingestion subsystem maintains its own DuckDB data store under
`market_data/openalgo/`; it does not alter the yfinance CSV cache or current backtests.
Configure `OPENALGO_HOST` and `OPENALGO_API_KEY` in `.env`, install the project
requirements, then run either equivalent command:

```bash
# Verify live NSE constituent access
python -m data_pipeline.openalgo_ingestion --action scrape

# One completed day / one stock smoke test
python -m data_pipeline.openalgo_ingestion --action download \
  --symbols RELIANCE --start-date 2026-08-20 --end-date 2026-08-20

# Root-level compatibility launcher
python openalgo_ingest.py --action read
```

For larger loads, use `--index NIFTY200 --days 365`; downloads are split into
broker-safe 30-day chunks. Successful chunks are skipped on a subsequent identical
run, while failed chunks are retried on the next run. Set
`OPENALGO_SHOONYA_APPEND_EQ=true` only if the connected broker requires `-EQ`
symbols. The stored data is available to research code through:

```python
from data_pipeline.openalgo_ingestion import BacktestDataReader

frame = BacktestDataReader().get_full_dataframe(symbol="RELIANCE")
close_matrix = BacktestDataReader().get_vectorbt_matrix()
```

#### Broker history boundary

Brokers serve intraday candles only up to a limited lookback. On every download,
the earliest servable date is detected automatically (binary search over small
probe windows against a liquid symbol) and cached in `download_state.sqlite`;
requested ranges reaching below that floor are clamped with a warning, and
ranges lying entirely below it are rejected cleanly. Pass `--refresh-boundary`
to force re-detection after broker-side changes. Tunables:
`OPENALGO_PROBE_SYMBOL` (default `RELIANCE`) and
`OPENALGO_PROBE_DELAY_SECONDS` (default `1`).

#### Browse the store visually

```bash
python visualizer/server.py   # opens http://127.0.0.1:8501
```

Candles + volume for any symbol/timeframe (1m–1d), SMA/EMA/VWAP overlays,
crosshair legend, CSV export, and a stale-aggregate warning banner.

### Data integrity & maintenance

**Volume sanitation (automatic).** Some broker feeds emit closing-auction
correction trades as negative volumes near 15:20 IST. The downloader clips
every incoming candle to `volume >= 0` before persisting, so the artifact
cannot enter the store again.

**Health check.** Run before backtests or after large downloads; exits `0`
when healthy, `1` when issues are found (cron-friendly):

```bash
python openalgo_ingest.py --action health
```

It verifies DuckDB connectivity, NULL/negative prices, NULL/negative
volumes, inverted high/low rows, duplicate candles, per-symbol coverage,
and chunk-state accounting from `download_state.sqlite`.

**Aggregates & archive.** Aggregates (`ohlcv_5m/15m/1h/1d`) are snapshots of
`ohlcv_1m` at build time — always rerun after new downloads:

```bash
python openalgo_ingest.py --action aggregate   # rebuild all timeframe tables
python openalgo_ingest.py --action archive     # export Parquet partitioned by year/month
```

Timeframe buckets are aligned to IST wall-clock boundaries (hourly buckets at
:00, daily candles stamped 00:00 IST) regardless of server timezone.

**Backup / restore.** Before any manual surgery on the store:

```bash
mkdir -p market_data/openalgo/backup
python -c "from data_pipeline.openalgo_ingestion import archive; archive.checkpoint()"
cp market_data/openalgo/backtest_data.duckdb market_data/openalgo/backup/backtest_data_pre_cleanup.duckdb
# restore = copy the backup file back over backtest_data.duckdb, then rerun --action aggregate
```

> **Historical note (2026-08-22):** 5,630 negative-volume rows (184 symbols)
> were found in the initial 365-day download and zeroed in place; aggregates
> were rebuilt from the cleaned store. The pre-cleanup snapshot is preserved
> at `market_data/openalgo/backup/backtest_data_pre_cleanup.duckdb`.

### Team distribution via Hugging Face

The store is mirrored to the private dataset `vaibhavfury/StockData` as
Hive-partitioned Parquet (`data/year=/month=`) plus a generated dataset card
and a standalone helper script at `tools/hf_stock_data.py`.

Publish a fresh snapshot (requires `HF_TOKEN` with write access):

```bash
python openalgo_ingest.py --action publish            # export + upload (~1 min)
python openalgo_ingest.py --action publish --repo other/repo
```

Teammates need only an HF token with read access — no clone of this repo:

```bash
pip install huggingface_hub duckdb pandas
export HF_TOKEN=hf_xxx

# fetch the helper from the dataset repo itself, then:
python hf_stock_data.py query                          # zero-download streaming SQL over HTTP
python hf_stock_data.py parquet --out ./stockdata --months 2026-07 2026-08   # filtered raw download
python hf_stock_data.py duckdb  --out ./stockdata --symbol RELIANCE --aggregates  # offline DuckDB
```

Timestamps are stored as UTC instants; partition columns follow IST calendar
months. Exchange data is licensed for internal use only — do not redistribute.

### Run Live Paper Trading Daemon:
```bash
python live_trading/paper_trader.py
```

### Run Automated Unit Test Suite (48 Tests):
```bash
python -m unittest discover tests
```

## 🗓️ Monthly Maintenance Routine

To keep your historical database fresh, optimized, and backed up, run this routine on the **1st of every month** (or after market hours on the last trading day of the month). 

Because the downloader tracks state in SQLite, it is completely idempotent—it will instantly skip previously downloaded history and only fetch the new month's data.

### Step 1: Download the New Month's Data
Run your standard lookback command. The engine will check the SQLite state DB, skip the past 11 months, and only request the new month's 1-minute candles from the broker.
```bash
python openalgo_ingest.py --action download --index NIFTY200 --days 365
```

#### Download the entire previous year (e.g., 2025)
```
python openalgo_ingest.py --action download --index NIFTY200 --start-date 2025-01-01 --end-date 2025-12-31
```

#### Download a specific month in the past
```
python openalgo_ingest.py --action download --index NIFTY200 --start-date 2025-06-01 --end-date 2025-06-30
```

#### Download a custom multi-year range
```
python openalgo_ingest.py --action download --index NIFTY200 --start-date 2024-01-01 --end-date 2025-12-31
```

### Step 2: Rebuild Timeframe Aggregates
Once the new 1-minute data is in DuckDB, regenerate your 5m, 15m, 1h, and 1d tables. This ensures your multi-year backtests can use the new data without querying the heavy 1-minute table.
```bash
python openalgo_ingest.py --action aggregate
```

### Step 3: Update the Parquet Archive (Cold Storage)
Export the updated DuckDB database to your compressed, partitioned Parquet archive. This serves as your disaster recovery backup and keeps your long-term storage highly optimized.
```bash
python openalgo_ingest.py --action archive
```

### Step 4: Verify Data Integrity (Optional)
Check the database stats to ensure the row count increased and there are no missing months.
```bash
python openalgo_ingest.py --action stats
```

> **Pro-Tip for Deep History:** If you want to backtest over 5 years, remember that most brokers restrict 1-minute data to the last 60-90 days. For deep history, temporarily change `INTERVAL = "1D"` or `"1H"` in `settings.py` and run the download command with `--days 1825` (5 years).