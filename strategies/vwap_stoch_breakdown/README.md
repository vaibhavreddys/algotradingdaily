# 📉 VWAP-Stochastic RSI Breakdown Strategy

**Strategy Family**: Intraday Momentum & Breakdown  
**Asset Class**: Indian Equities (NSE / BSE)  
**Default Resolution**: 15-minute Candles (`TIMEFRAME = '15m'`)  
**Active Production Version**: `v1.0.0` (implemented in `v1_0.py`)  

---

## 📜 Version Evolution & Changelog

### 🔹 `v1.0.0` (Baseline Stable) — *August 2026*
* **Core Hypothesis**:
  * Stocks exhibiting **Relative Weakness** against the broader NIFTY benchmark that trade below their intraday volume-weighted average price (**VWAP**) will accelerate downward when short-term momentum rolls over from an overbought reading.
* **Timing & Session Lifecycle**:
  * **Warmup Offset**: `45 minutes` after market open (`10:00 AM IST` on NSE) to avoid opening noise and volatile morning spreads.
  * **Cutoff Offset**: `120 minutes` before market close (`1:30 PM IST` on NSE) — stops taking new entries to ensure sufficient time for trade maturation.
  * **Mandatory Auto-Squareoff**: `30 minutes` before market close (`3:00 PM IST` on NSE) — forces market exit for all open intraday positions before broker RMS penalty kicks in.
* **Entry Filters (All 4 Required)**:
  1. `Close < VWAP` (Bearish intraday structure)
  2. `ADX(14) > 25.0` (Trend strength confirmation)
  3. `Stoch_K_prev >= 80` and `Stoch_K < 80` (Stochastic RSI overbought rollover)
  4. `Stock Return % < NIFTY Benchmark Return %` (Macro Relative Weakness)
* **Risk & Trade Management**:
  * **Stop-Loss**: 3-bar Swing High + 0.05% anti-wick buffer (with a minimum floor of 0.20% above entry).
  * **Target**: 1:2 Risk-Reward Ratio ($Target = Entry - 2 	imes Risk$).
  * **Trailing Stop**: When price reaches $+1R$ profit, Stop-Loss is automatically trailed to **Breakeven** ($Entry Price$).
* **Backtest Summary (10-Month DuckDB Dataset | Oct 2025 – Aug 2026)**:
  * **NIFTY 50 Universe**: 344 Trades | **44.19% Win Rate** | **+41.53% Gross Profit (Pre-Tax)** | Profit Factor **1.22**.
  * **Profitable Months**: Jan 2026, Feb 2026, Apr 2026, May 2026, Jul 2026.

---

## 📁 Directory Structure
* `__init__.py`: Strategy package router pointing to the stable production version.
* `v1_0.py`: Concrete `VWAPStochBreakdownStrategy` implementation for `v1.0.0`.
* `README.md`: This documentation and reverse-chronological changelog.
