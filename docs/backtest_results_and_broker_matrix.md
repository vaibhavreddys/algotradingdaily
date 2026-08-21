# Backtest Results & Multi-Broker Friction Matrix

This document provides quantitative backtest results, timeframe comparison studies, and the statutory multi-broker friction matrix.

---

## 📊 59-Day Baseline Performance (15-Minute Timeframe)

Simulation run on 50 Nifty constituents across **59 Trading Days (2026-06-01 to 2026-08-21)** with ₹10,000 capital, dynamic compounding, and max 2 concurrent positions:

| Metric | Quantitative Value |
| :--- | :--- |
| **Initial Capital** | ₹10,000.00 |
| **Max Simultaneous Trades** | 2 Positions (Equal Capital Split) |
| **Intraday MIS Leverage** | 5x |
| **Total Trades Taken** | 122 (56 Wins / 66 Losses) |
| **Win Rate** | **45.90%** |
| **Gross Realized Profit** | **+₹4,580.69 (+45.81%)** |
| **Total Statutory Taxes & Charges** | **₹2,660.65** |
| **Net Realized Profit (Post-Charges)** | **+₹1,920.04 (+19.20% Net Return)** |
| **Ending Capital Balance** | **₹11,920.04** |
| **Profit Factor** | **1.51** (Gross Gains / Gross Losses) |
| **Max Drawdown (MDD)** | **₹1,429.20 (-11.81%)** |
| **Max Equity Runup** | **+₹3,439.18 (+34.39%)** *(Trough-to-Peak Surge)* |
| **Win / Loss Streaks** | **5 Wins** / **9 Losses** *(Max Consecutive)* |
| **Largest Trade** | **+₹1,104.28 Win** / **-₹772.39 Loss** |
| **Trade Expectancy** | **+₹15.74 / trade** |
| **Avg Win / Avg Loss** | **+₹241.72 / -₹135.69** |

---

## ⏱️ Outcome Distribution Breakdown

Across the 122 executed trades:
* **3:00 PM Auto-Squareoff ⏱️**: 69 trades (**56.6%**)
* **Stop Loss Hit ❌**: 34 trades (**27.9%**)
* **1:2 Target Hit ✅**: 11 trades (**9.0%**)
* **+1R Trailing SL Hit (Breakeven) 🛡️**: 8 trades (**6.6%**)

---

## 🏦 Multi-Broker Friction Comparison Matrix

Calculated across the 122 executed trades modeling all Indian regulatory statutory taxes (STT 0.025% sell-side, NSE Txn 0.00297%, GST 18%, Stamp Duty 0.003%, SEBI 0.0001%) under dynamic compounding turnover:

| Broker Schedule | Total Taxes / Fees | Net Realized PnL | Net ROI % | Fee Impact Analysis |
| :--- | :---: | :---: | :---: | :--- |
| **Zero-Brokerage Baseline** | ₹1,221.05 | +₹3,359.64 | **+33.60%** | Pure statutory government taxes |
| **Shoonya (Finvasia)** | **₹2,660.65** | **+₹1,920.04** | **+19.20%** | **Optimal Real-World (Zero Brokerage)** |
| **Zerodha** (₹20 / order) | ₹3,672.85 | +₹907.84 | **+9.08%** | -10.12% ROI lost to order brokerage |
| **Dhan** (₹20 / order) | ₹3,672.85 | +₹907.84 | **+9.08%** | -10.12% ROI lost to order brokerage |
| **Fyers** (₹20 / order) | ₹3,672.85 | +₹907.84 | **+9.08%** | -10.12% ROI lost to order brokerage |
| **Groww** (0.05% max ₹20) | ₹5,307.39 | -₹726.70 | **-7.27%** | -26.47% ROI drag (Turns profitable strategy negative) |
| **Upstox** (₹20 / order) | ₹5,307.39 | -₹726.70 | **-7.27%** | -26.47% ROI drag |
| **Angel One** (₹20 / order) | ₹6,979.45 | -₹2,398.76 | **-23.99%** | Severe friction drag on small-capital intraday accounts |
