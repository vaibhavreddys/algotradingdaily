# Strategy Specification: VWAP-Stochastic RSI Breakdown

## 1. Strategy Overview

| Parameter | Specification |
| :--- | :--- |
| **Strategy Name** | VWAP-Stochastic RSI Breakdown |
| **Asset Class** | Indian Equities (NSE Cash) |
| **Trade Direction** | **Short Only** (Intraday MIS) |
| **Trading Universe** | NIFTY 50 Constituents |
| **Timeframe** | **15-Minute Candles** |
| **Leverage** | 5x Intraday MIS Leverage |
| **Max Concurrent Positions** | 2 Simultaneous Trades (Equal Capital Allocation) |
| **Daily Loss Limit** | **4% Max Daily Loss Circuit Breaker** |
| **Risk per Trade** | **1% Fixed Capital Risk (Dual-Guard Sizing)** |

---

## 2. Quantitative Indicators & Mathematical Formulations

### A. Intraday VWAP (Volume Weighted Average Price)
Calculated continuously from market open (09:15 AM IST) resets daily:
$$\text{VWAP}_t = \frac{\sum_{i=1}^{t} (\text{Typical Price}_i \times \text{Volume}_i)}{\sum_{i=1}^{t} \text{Volume}_i}$$

### B. Fast Stochastic RSI (%K)
Calculated using 14-period RSI and 14-period Stochastic smoothing:
$$\text{StochRSI} = \frac{\text{RSI}_{14} - \min(\text{RSI}_{14}, 14)}{\max(\text{RSI}_{14}, 14) - \min(\text{RSI}_{14}, 14)}$$
$$\%K = \text{SMA}(\text{StochRSI}, 3) \times 100$$

### C. Average Directional Index (ADX)
14-period standard Welles Wilder ADX for trend strength confirmation:
$$\text{ADX}_{14} > 25 \quad (\text{Filters out sideways non-trending consolidation})$$

### D. Relative Weakness Filter
Calculated against NIFTY 50 Benchmark index (`^NSEI`):
$$\text{Stock \% Change} = \frac{\text{Close}_t - \text{Open}_{\text{day}}}{\text{Open}_{\text{day}}}$$
$$\text{NIFTY \% Change} = \frac{\text{NIFTY}_t - \text{NIFTY}_{\text{day open}}}{\text{NIFTY}_{\text{day open}}}$$
$$\text{Relative Weakness} = \text{Stock \% Change} < \text{NIFTY \% Change}$$

---

## 3. Entry Signal Rules

A short trade signal triggers on the close of a 15-minute candle if **ALL** 5 conditions hold:
1. **Entry Window**: Current time is between **10:00 AM and 1:30 PM IST**.
2. **Relative Weakness**: Stock is weaker than NIFTY 50 index from day open.
3. **Price below VWAP**: $\text{Close}_t < \text{VWAP}_t$.
4. **Strong Trend**: $\text{ADX}_{14} > 25$.
5. **Stochastic RSI Breakdown**: $\%K_{t-1} \ge 80$ and $\%K_t < 80$.

---

## 4. Risk Management & Position Sizing Architecture

### Dual-Guard Capital Sizing
Share allocation ensures that trade risk does not exceed 1% of total capital while remaining strictly within margin limits:
$$\text{Quantity} = \min\left(\left\lfloor \frac{\text{Capital} \times 0.01}{\text{SL Distance}} \right\rfloor, \ \left\lfloor \frac{\text{Slot Margin} \times 5}{\text{Entry Price}} \right\rfloor\right)$$

### Stop Loss & Target Formulations
* **Initial Stop Loss**:
  $$\text{SL Price} = \max\left(\text{Swing High}_{\text{prev 5 bars}} \times 1.001, \ \text{Entry Price} \times 1.005\right)$$
* **1:2 Risk-to-Reward Target**:
  $$\text{Target Price} = \text{Entry Price} - (2 \times \text{Initial Risk})$$
* **Trailing Stop Loss (+1R Rule)**:
  * When price drops to $\text{Entry Price} - 1\text{R}$, SL trails immediately to **Breakeven** ($\text{Entry Price} \times 1.002$).
* **4% Daily Loss Circuit Breaker**:
  * If cumulative realized loss today reaches 4% of day-starting capital, new scans are halted immediately.
* **Mandatory EOD Squareoff**:
  * All open positions are squared off at **3:00 PM IST** before broker-imposed squareoff.
