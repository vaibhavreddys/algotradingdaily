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
│   └── data_feed.py       # Smart local caching, silent scans & live tick fetcher
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

### Run Live Paper Trading Daemon:
```bash
python live_trading/paper_trader.py
```

### Run Automated Unit Test Suite (48 Tests):
```bash
python -m unittest discover tests
```
