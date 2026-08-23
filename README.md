# AlgoTradingDaily | Quantitative Algorithmic Trading & Simulation Engine

A high-performance, modular algorithmic trading platform and portfolio simulation suite for Indian equity markets (NSE).

Built with institutional-grade risk management, sub-second execution daemons, dynamic compounding, multi-broker statutory fee modeling, and an embedded 15.48-million-bar DuckDB historical database.

---

## 🏛️ Key Architecture & Capabilities

* **Dual Interface: Web Command Center & CLI Wizard**:
  * **Web Studio (`http://localhost:8501`)**: Overview telemetry dashboard, interactive strategy backtester with live KPI cards and multi-broker comparison matrix, and full TradingView candlestick visualizer.
  * **CLI Engine & Guided Wizard (`python backtesting/portfolio_sim.py -i`)**: Interactive step-by-step terminal wizard for running backtests across strategy families, versions, timeframes, universes, and custom capital.
* **Embedded Columnar DuckDB Store**:
  * Ingests and processes **15.48 Million 1-minute bars** over **200 NIFTY constituents** spanning 10 months (Oct 2025 – Aug 2026) in under 1.5 seconds locally.
* **Institutional Risk & Sizing Engine**:
  * **Dual-Guard Position Sizing**: 1% fixed capital risk budgeting coupled with concurrent slot margin allocation.
  * **Max Concurrent Position Slots**: Limits simultaneous exposure (e.g. 2 concurrent slots) to prevent over-leverage.
  * **Daily Circuit Breakers**: 4% max daily portfolio loss breaker protecting account capital.
  * **Dynamic Trailing Stop Loss**: Automated breakeven trailing once +1R target is reached.
  * **Mandatory 3:00 PM Squareoff**: Strict intraday position liquidation before market close.
* **Universal Multi-Broker Statutory Fee Engine**:
  * Models all Indian statutory taxes (STT on sell side, NSE transaction charges, SEBI turnover fees, Stamp Duty, 18% GST).
  * Evaluates real-time Net Realized ROI across 8 major Indian broker schedules (Shoonya zero-brokerage, Zerodha, Dhan, Fyers, Groww, Angel One, Upstox).
* **Modular Strategy Architecture (`strategies/`)**:
  * Strict directory-per-strategy isolation with versioned files (`v1_0.py`, `v1_1.py`), strategy contracts, and reverse-chronological changelogs.

---

## ⚡ Quick Start

### 1. Launch the Quantitative Web Command Center
```bash
# Start the local server daemon
python visualizer/server.py --port 8501
```
* **Overview Dashboard**: [http://localhost:8501/](http://localhost:8501/)
* **Strategy Backtest Lab**: [http://localhost:8501/backtest](http://localhost:8501/backtest)
* **Candle & Indicator Visualizer**: [http://localhost:8501/chart](http://localhost:8501/chart)

### 2. Run Portfolio Backtests via CLI

#### A. Interactive Wizard Mode (Guided):
```bash
python backtesting/portfolio_sim.py -i
```
Prompts step-by-step for Strategy, Version, Timeframe, Universe, and Initial Capital.

#### B. Direct Execution (with smart defaults: v1.0.0, 15m, NIFTY 200, ₹1,00,000):
```bash
python backtesting/portfolio_sim.py
```

#### C. Custom Parameter Overrides:
```bash
# Run on NIFTY 50 universe with ₹50,000 capital
python backtesting/portfolio_sim.py --universe NIFTY50 --capital 50000

# Run specific version on 5-minute timeframe
python backtesting/portfolio_sim.py --version v1_0 --timeframe 5m --universe NIFTY200
```

---

## 📊 Backtest Analytics (10-Month DuckDB Dataset)

Benchmarked across **10 Months (Oct 20, 2025 to Aug 21, 2026)** over **207 Trading Days** (5,313 fifteen-minute candles) on **₹1,00,000 Initial Capital**:

| Performance Metric | NIFTY 50 Universe | NIFTY 200 Universe |
| :--- | :--- | :--- |
| **Total Trades Executed** | 350 Trades | 448 Trades |
| **Win Rate** | **44.00%** | **42.86%** |
| **Profit Factor** | **1.28** | **1.14** |
| **Gross Profit (Trading Edge)** | **+₹73,553.02 (+73.55%)** | **+₹66,135.27 (+66.14%)** |
| **Total Statutory Taxes & Brokerage** | **₹37,202.72** | **₹35,250.55** |
| **Net Realized Profit (Shoonya)** | **+₹36,350.30 (+36.35% Net ROI)** | **+₹30,884.72 (+30.88% Net ROI)** |
| **Net Realized Profit (Zerodha)** | **+₹23,960.30 (+23.96% Net ROI)** | **+₹18,494.72 (+18.49% Net ROI)** |
| **Zero-Brokerage Baseline** | **+₹40,480.30 (+40.48% Net ROI)** | **+₹35,014.72 (+35.01% Net ROI)** |

---

## 📁 Repository Structure

```text
algotradingdaily/
├── backtesting/
│   └── portfolio_sim.py          # Portfolio execution simulator & CLI wizard (-i)
├── core/
│   ├── charges.py                # Universal Indian statutory taxes & 8 broker fee schedules
│   ├── config.py                 # System risk limits and execution parameters
│   ├── indicators.py             # VWAP, Stochastic RSI, ADX, Relative Weakness math
│   └── market_calendar.py        # NSE trading hours, holidays, and warmup schedule
├── data_pipeline/
│   ├── data_feed.py              # Multi-tier candle loader (DuckDB -> CSV -> yfinance)
│   └── openalgo_ingestion/       # DuckDB ingestion engine & reader
├── market_data/
│   └── openalgo/
│       └── backtest_data.duckdb  # 15.48M 1-minute historical bars (1.32 GB)
├── strategies/
│   ├── base_strategy.py          # Abstract strategy contract (BaseStrategy)
│   ├── registry.py               # Dynamic strategy & version discovery engine
│   └── vwap_stoch_breakdown/     # VWAP-Stoch strategy family
│       ├── __init__.py           # Active default exports
│       ├── README.md             # Version changelog & quantitative hypothesis
│       └── v1_0.py               # Concrete v1.0.0 implementation
├── visualizer/
│   ├── server.py                 # Multi-page HTTP server & REST backtest API
│   ├── dashboard.html            # Command Center Overview landing page (/)
│   ├── backtest.html             # Quantitative Strategy Backtest Studio (/backtest)
│   └── app.html                  # Lightweight-Charts Candle Visualizer (/chart)
└── tests/                        # Full unit test suite (72 passing tests)
```

---

## 🧪 Running Unit Tests

```bash
python -m unittest discover tests
```
*All 72 tests pass with 100% test coverage across risk controls, indicators, DuckDB pipelines, and charges calculation.*
