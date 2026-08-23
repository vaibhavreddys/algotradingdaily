# 📊 Quantitative Backtest Results & Multi-Broker Performance Matrix

Comprehensive benchmark simulation of the **VWAP-Stoch Breakdown Strategy (v1.0.0)** executed across **10 Months (Oct 20, 2025 to Aug 21, 2026)** over **207 Trading Days** (5,313 fifteen-minute candles) using the embedded **15.48-million-bar DuckDB store**.

---

## 🏛️ Simulation Setup & Risk Rules

* **Initial Capital**: ₹1,00,000.00
* **Max Concurrent Position Slots**: 2 Slots (Max ₹50,000 margin per position)
* **Risk Budget**: 1% capital risk per trade
* **Dynamic Sizing**: 5x Intraday MIS Leverage
* **Stop Loss**: Structural Swing High (minimum 0.3% buffer)
* **Take Profit Target**: 1.5R Risk-to-Reward Ratio
* **Dynamic Trailing**: Break-even stop loss upon reaching +1R favorable excursion
* **Day-End Liquidation**: Mandatory 3:00 PM auto-squareoff

---

## 📈 Performance Summary

| Performance Metric | NIFTY 50 Universe | NIFTY 200 Universe |
| :--- | :--- | :--- |
| **Total Trades Executed** | 350 Trades | 448 Trades |
| **Winning Trades** | 154 Trades (44.00%) | 192 Trades (42.86%) |
| **Losing Trades** | 196 Trades (56.00%) | 256 Trades (57.14%) |
| **Gross Win/Loss Profit Factor** | **1.28** | **1.14** |
| **Gross Realized Profit** | **+₹73,553.02 (+73.55%)** | **+₹66,135.27 (+66.14%)** |
| **Total Statutory Taxes & Brokerage** | **₹37,202.72** | **₹35,250.55** |
| **Net Realized Profit (Shoonya Zero-Brokerage)** | **+₹36,350.30 (+36.35% Net ROI)** | **+₹30,884.72 (+30.88% Net ROI)** |
| **Net Realized Profit (Standard Discount Brokers)** | **+₹23,960.30 (+23.96% Net ROI)** | **+₹18,494.72 (+18.49% Net ROI)** |

---

## 🏛️ Multi-Broker Net Profit Comparison Matrix

Simulation comparing exact net take-home profit after accounting for STT (0.025% sell side), NSE transaction charges (0.00297%), SEBI turnover fees, Stamp Duty (0.003% buy side), 18% GST, and broker-specific commissions on **₹1,00,000 Starting Capital**:

### NIFTY 50 Universe (350 Trades)
| Broker Schedule | Total Statutory Taxes & Brokerage | Net Realized PnL (₹) | Net Realized ROI % |
| :--- | :--- | :--- | :--- |
| **Zero-Brokerage Baseline** | ₹33,072.72 | **+₹40,480.30** | **+40.48%** |
| **Shoonya (Finvasia)** | ₹37,202.72 | **+₹36,350.30** | **+36.35%** |
| **Zerodha** | ₹49,592.72 | **+₹23,960.30** | **+23.96%** |
| **Dhan** | ₹49,592.72 | **+₹23,960.30** | **+23.96%** |
| **Fyers** | ₹49,592.72 | **+₹23,960.30** | **+23.96%** |
| **Groww** | ₹49,592.72 | **+₹23,960.30** | **+23.96%** |
| **Angel One** | ₹49,592.72 | **+₹23,960.30** | **+23.96%** |
| **Upstox** | ₹49,592.72 | **+₹23,960.30** | **+23.96%** |

### NIFTY 200 Universe (448 Trades)
| Broker Schedule | Total Statutory Taxes & Brokerage | Net Realized PnL (₹) | Net Realized ROI % |
| :--- | :--- | :--- | :--- |
| **Zero-Brokerage Baseline** | ₹31,120.55 | **+₹35,014.72** | **+35.01%** |
| **Shoonya (Finvasia)** | ₹35,250.55 | **+₹30,884.72** | **+30.88%** |
| **Zerodha** | ₹47,640.55 | **+₹18,494.72** | **+18.49%** |
| **Dhan** | ₹47,640.55 | **+₹18,494.72** | **+18.49%** |
| **Fyers** | ₹47,640.55 | **+₹18,494.72** | **+18.49%** |
| **Groww** | ₹47,640.55 | **+₹18,494.72** | **+18.49%** |
| **Angel One** | ₹47,640.55 | **+₹18,494.72** | **+18.49%** |
| **Upstox** | ₹47,640.55 | **+₹18,494.72** | **+18.49%** |

---

## 💡 Key Quantitative Findings

1. **Broker Friction Impact**:
   * On high-frequency intraday momentum strategies, using a zero-brokerage broker (Shoonya) yields **+₹12,390 additional net profit** compared to standard ₹20/order discount brokers on a ₹100,000 account over 10 months.
2. **Universe Selectivity**:
   * NIFTY 50 large-caps deliver higher gross profit (+73.55% vs +66.14%) and higher win rate (44.00% vs 42.86%) than the broader NIFTY 200 universe due to tighter spreads and higher institutional liquidity at VWAP breakdown levels.
