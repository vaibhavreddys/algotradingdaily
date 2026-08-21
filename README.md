# Shoonya Algorithmic Trading Bot & Simulation Engine

An automated intraday quantitative trading execution engine and portfolio simulator built in Python for Shoonya (Finvasia).

---

## 📈 Quick Strategy Overview: VWAP-Stoch Breakdown

* **Universe**: NIFTY 50 Constituents (NSE Equities)
* **Timeframe**: 15-Minute Candles (:00, :15, :30, :45)
* **Entry Window**: 10:00 AM – 1:30 PM IST (Short Breakdown)
* **Core Logic**: Stochastic RSI overbought exit (`%K < 80`) + Trend Filter (`ADX > 25`) + VWAP Confirmation (`Close < VWAP`) + NIFTY 50 Relative Weakness.
* **Risk & Exits**: 3-bar swing high Stop-Loss $\to$ Dynamic +1R Trailing SL to Breakeven $\to$ 1:2 R:R Target $\to$ 3:00 PM Auto-Squareoff.

> 📖 **Full Math & Strategy Rules**: See [`docs/strategy_specification.md`](docs/strategy_specification.md)

---

## 📊 Backtest Performance (58-Day Simulation)

| Metric | Result |
| :--- | :--- |
| **Simulation Period** | **2026-06-01 to 2026-08-21 (59 Trading Days)** |
| **Initial Capital** | ₹10,000.00 (Max 2 concurrent positions, 5x MIS leverage, Dynamic Compounding) |
| **Total Trades Taken** | 122 (56 Wins / 66 Losses) |
| **Win Rate** | **45.90%** |
| **Net Realized Profit** | **+₹1,920.04 (+19.20% ROI)** *(Post-all Shoonya brokerage & statutory taxes)* |
| **Profit Factor / MDD** | **1.51** Profit Factor | **-11.81%** Max Drawdown |
| **Trade Expectancy** | **+₹15.74** / trade |

> 📊 **Multi-Timeframe Study & Broker Friction Matrix**: See [`docs/backtest_results_and_broker_matrix.md`](docs/backtest_results_and_broker_matrix.md)

---

## 📁 Repository Structure

```text
shoonya_algo/
├── config.py              # Centralized TradingConfig dataclass & parameters
│
├── core/                  # Pure math, indicators & regulatory fee calculators
│   ├── trade_db.py        # Isolated SQLite trade journals (WAL mode & 2-decimal precision)
│   ├── indicators.py      # Stoch RSI, ADX, VWAP, Relative Weakness formulas
│   └── charges.py         # Universal Indian taxes & multi-broker fee engine
│
├── strategies/            # Strategy rules, signal extraction & trade lifecycle
│   └── vwap_stoch_breakdown.py
│
├── data_pipeline/         # Market data gateway & high-frequency tick caching
│   ├── data_feed.py       # Smart local caching & live tick fetcher
│   └── shoonya_loader.py  # Shoonya 1-year historical downloader
│
├── backtesting/           # Historical simulation & scanning engines
│   ├── portfolio_sim.py   # Chronological multi-stock portfolio simulator
│   └── scanner.py         # Unconstrained single-stock indicator scanner
│
├── live_trading/          # Execution engines
│   ├── base_engine.py     # Common scheduler, candle aggregator & timing rules
│   ├── paper_trader.py    # High-frequency guardian virtual paper trading
│   └── live_trader.py     # Real-money Shoonya OMS order placement
│
├── alerts/                # Multi-channel notification framework
│   ├── __init__.py        # Public export facade
│   ├── base.py            # BaseAlertChannel interface & dynamic channel dispatcher
│   └── telegram.py        # Telegram bot push notifications
│
├── tests/                 # Automated test suite (20 unit tests)
│   ├── test_trade_db.py   # SQLite CRUD, isolation & zero-pollution verification
│   ├── test_charges.py    # Multi-broker fee engine & statutory tax verification
│   ├── test_strategy_parity.py # Exact numerical parity between backtest and live
│   ├── test_alerts.py     # Multi-channel notification dispatch & failure isolation
│   └── test_live_monitor.py # High-frequency position guardian & 3PM exit resolution
│
├── docs/                  # Detailed documentation & operational guides
│   ├── cloud_execution_setup_guide.md         # 24/7 cloud daemon & Telegram bot setup
│   ├── broker_configuration_guide.md          # Multi-broker API keys, TOTP & .env guide
│   ├── strategy_specification.md              # Complete math, rules & indicator logic
│   └── backtest_results_and_broker_matrix.md  # Multi-timeframe & broker friction study
│
├── market_data/           # Local candle cache CSVs (git-ignored)
├── database/              # SQLite trade journals (git-ignored)
├── .env.example           # Public secrets template with placeholder values
├── .env                   # Private broker credentials (git-ignored)
└── README.md
```

---

## 🚀 Setup & Quickstart

### 1. Clone & Setup Environment:
```bash
git clone https://github.com/vaibhavreddys/algotradingdaily.git
cd algotradingdaily

# Setup Python 3.12 Virtual Environment
python -m venv venv
.\venv\Scripts\activate   # Windows
# source venv/bin/activate # Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Credentials:
```bash
Copy-Item .env.example .env   # Windows
# cp .env.example .env        # Linux/macOS
```
> 🔐 **Broker Setup Guide**: Detailed instructions for Shoonya, Zerodha, Dhan, Angel One, Upstox, and Fyers are in [`docs/broker_configuration_guide.md`](docs/broker_configuration_guide.md).

---

## 💻 Usage

### Run Portfolio Simulation (₹10,000 Capital, 2 Slots):
```bash
python -m backtesting.portfolio_sim            # Fast load from local market_data/ cache
python -m backtesting.portfolio_sim --refresh  # Force fresh parallel re-download (~6s)
```

### Run Unconstrained Strategy Scanner:
```bash
python -m backtesting.scanner
```

### Run Live Paper Trading Engine:
```bash
python -m live_trading.paper_trader
```

### Run Automated 24/7 Cloud Daemon (GitHub Actions + Telegram):
* Operates automatically Monday to Friday (09:10 AM – 3:30 PM IST) in the cloud with live push alerts.
* Setup guide: [`docs/cloud_execution_setup_guide.md`](docs/cloud_execution_setup_guide.md).

### Run Test Suite:
```bash
python -m unittest discover tests
```
