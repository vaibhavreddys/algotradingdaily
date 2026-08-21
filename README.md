# Shoonya Algorithmic Trading Bot & Simulation Engine

An automated, quantitative algorithmic trading bot and backtesting simulation suite tailored for Indian equity markets on the **Shoonya (Finvasia)** brokerage platform.

The system trades the **VWAP-Stochastic RSI Breakdown Strategy** with **Nifty 50 Relative Weakness** filtering on 15-minute intraday candles.

---

## 📈 Quick Strategy Overview: VWAP-Stoch Breakdown

* **Direction**: Intraday Short Only (MIS Equity).
* **Universe**: NIFTY 50 Constituents.
* **Execution Timeframe**: 15-Minute Candles.
* **Entry Conditions**:
  1. **Relative Weakness**: Stock intraday % change from open is weaker than NIFTY 50 index.
  2. **Trend Filter**: 15m Close is below Intraday VWAP (`Close < VWAP`).
  3. **Trend Strength**: 14-period ADX > 25 (Strong directional downtrend).
  4. **Momentum Breakdown**: Fast Stochastic RSI %K crosses down through 80 overbought threshold (`%K[prev] >= 80` and `%K < 80`).
  5. **Entry Window**: 10:00 AM to 1:30 PM IST (avoids opening noise and late-day consolidation).
* **Risk Management & Position Sizing**:
  * **Dual-Guard Capital Sizing**: $\text{Quantity} = \min\left(\left\lfloor \frac{\text{Capital} \times 0.01}{\text{SL Distance}} \right\rfloor, \ \left\lfloor \frac{\text{Slot Margin} \times 5}{\text{Entry Price}} \right\rfloor\right)$
  * **Max Concurrent Trades**: 2 simultaneous positions with equal capital split.
  * **Daily Circuit Breaker**: 4% Max Daily Loss threshold (`MAX_DAILY_LOSS_PCT = 0.04`).
  * **Trailing Stop Loss**: Moves to Breakeven (+0.2% limit buffer) upon reaching +1R profit.
  * **Dynamic 1:2 R:R**: Target set at $2 \times \text{Initial Risk}$.
  * **Hard EOD Auto-Squareoff**: 3:00 PM IST.

---

## 📊 Backtest Performance (59-Day Simulation)

Simulation run across **59 Trading Days (2026-06-01 to 2026-08-21)** with **₹10,000 Starting Capital**, dynamic compounding, statutory fee models, and 4% daily circuit breaker:

| Metric | Backtest Result |
| :--- | :--- |
| **Initial Capital** | ₹10,000.00 |
| **Max Concurrent Positions** | 2 Simultaneous Trades (Equal Split) |
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

> 📚 **Detailed Studies & Broker Matrix**: See [`docs/backtest_results_and_broker_matrix.md`](docs/backtest_results_and_broker_matrix.md)

---

## 🏗️ Repository Structure

```text
shoonya_algo/
├── config.py              # Centralized TradingConfig dataclass & risk thresholds
│
├── core/                  # Core analytics, risk controls, fees & persistence
│   ├── risk.py            # Stop/target calculations, dual-guard sizing & circuit breaker
│   ├── capital.py         # Slot margin calculations & persistent paper balances
│   ├── trade_db.py        # Isolated SQLite trade journals (WAL mode & atomic balance ledger)
│   ├── indicators.py      # Stoch RSI, ADX, VWAP, Relative Weakness formulas
│   └── charges.py         # Universal Indian taxes & multi-broker fee engine
│
├── strategies/            # Strategy rules, sub-filter boolean flags & simulation
│   └── vwap_stoch_breakdown.py
│
├── data_pipeline/         # Market data gateway & high-frequency tick caching
│   ├── data_feed.py       # Smart local caching, silent scans & live tick fetcher
│   └── shoonya_loader.py  # Shoonya 1-year historical downloader
│
├── backtesting/           # Historical simulation & scanning engines
│   ├── portfolio_sim.py   # Chronological multi-stock portfolio simulator with compounding
│   └── scanner.py         # Unconstrained single-stock indicator scanner
│
├── live_trading/          # Execution engines & schedulers
│   ├── base_engine.py     # Base engine with sleep inhibitor, funnel renderer & reconciler
│   ├── paper_trader.py    # High-frequency guardian virtual paper trading daemon
│   └── live_trader.py     # Real-money Shoonya OMS order placement engine
│
├── alerts/                # Multi-channel notification framework
│   ├── __init__.py        # Public export facade
│   ├── base.py            # BaseAlertChannel interface & dynamic channel dispatcher
│   └── telegram.py        # Telegram bot push notifications
│
├── docs/                  # In-depth architectural & operational documentation
│   ├── strategy_specification.md
│   ├── backtest_results_and_broker_matrix.md
│   ├── broker_configuration_guide.md
│   └── cloud_execution_setup_guide.md
│
└── tests/                 # Comprehensive unit test suite (48 tests)
    ├── test_risk.py
    ├── test_funnel_telemetry.py
    ├── test_sleep_prevention.py
    ├── test_timing_scheduler.py
    ├── test_live_reconciliation.py
    └── ...
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

### 2. Configure Credentials:
```bash
cp .env.example .env
```
Fill in your credentials in `.env`:
* `SHOONYA_USER`: Shoonya Client ID
* `SHOONYA_PWD`: Shoonya Trading Password
* `SHOONYA_API_KEY`: Shoonya API Key
* `SHOONYA_VENDOR_CODE`: Shoonya Vendor Code
* `SHOONYA_TOTP_KEY`: TOTP Secret Key
* `TELEGRAM_BOT_TOKEN` & `TELEGRAM_CHAT_ID`: (Optional) Instant push alerts

---

## ⚡ Usage

### Run Portfolio Simulation (Compounded ₹10,000, 2 Slots):
```bash
python backtesting/portfolio_sim.py
```

### Run Live Paper Trading Engine:
```bash
python live_trading/paper_trader.py
```

### Run Test Suite (48 Tests):
```bash
python -m unittest discover tests
```
