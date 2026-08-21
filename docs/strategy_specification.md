# Strategy Specification: VWAP-Stoch Breakdown

This document provides the complete mathematical definitions, entry/exit rules, and risk management logic for the **VWAP-Stoch Breakdown** intraday trading strategy.

---

## 📈 Strategy Core Architecture

* **Universe**: NIFTY 50 Constituents (NSE Equities)
* **Timeframe**: 15-Minute Candles
* **Direction**: Intraday Short Breakdown (Equities MIS)
* **Active Entry Window**: 10:00 AM – 1:30 PM IST
* **Mandatory Square-off**: 15:00 IST (3:00 PM)

---

## 🔍 Technical Indicators & Entry Rules

A short entry signal triggers on the close of a 15-minute candle if and only if **all 4 conditions** are satisfied simultaneously:

### 1. Stochastic RSI Momentum Filter
* **Parameters**: Length = 14, RSI Length = 14, %K = 3, %D = 3
* **Condition**: `%K` crosses below 80 from the overbought zone within the last 2 bars:
  $$\text{Stoch\_K}[t] < 80 \quad \text{and} \quad \text{Stoch\_K}[t-1] \ge 80$$

### 2. ADX Trend Strength Filter
* **Parameters**: Length = 14
* **Condition**: Trend strength exceeds threshold:
  $$\text{ADX}[t] > 25$$

### 3. VWAP Confirmation
* **Condition**: Closing price is trading below the intraday Volume-Weighted Average Price:
  $$\text{Close}[t] < \text{VWAP}[t]$$

### 4. NIFTY 50 Relative Weakness Filter
* **Calculation**:
  $$\Delta_{\text{Stock}}[t] = \frac{\text{Close}_{\text{Stock}}[t] - \text{Open}_{\text{Day\_Stock}}}{\text{Open}_{\text{Day\_Stock}}}$$
  $$\Delta_{\text{Nifty}}[t] = \frac{\text{Close}_{\text{Nifty}}[t] - \text{Open}_{\text{Day\_Nifty}}}{\text{Open}_{\text{Day\_Nifty}}}$$
* **Condition**: Stock is underperforming the broader market benchmark:
  $$\Delta_{\text{Stock}}[t] < \Delta_{\text{Nifty}}[t]$$

---

## 🛡️ Risk Management & Position Sizing

### 1. Dynamic Capital Sizing & Equal Split Compounding
* **Baseline Capital**: ₹10,000
* **Max Concurrent Positions**: Configurable (Default: 2 open slots)
* **Dynamic Slot Margin**: $\text{Slot Margin} = \frac{\text{Current Account Capital}}{\text{Max Concurrent Slots}}$
* **Exposure with 5x MIS Leverage**: $\text{Exposure} = \text{Slot Margin} \times \text{LEVERAGE\_MIS (5x)}$
* **Quantity Sizing**:
  $$\text{Quantity} = \max\left(1, \left\lfloor \frac{\text{Dynamic Exposure}}{\text{Entry Price}} \right\rfloor\right)$$

### 2. 4% Daily Portfolio Loss Circuit Breaker
* **Rule**: If today's cumulative realized net PnL reaches $-4.0\%$ of morning opening capital, the trading daemon halts all new entries for the remainder of the session to protect against hostile whipsaw days.

### 2. Stop Loss & Target Calculation
* **Swing High Lookback**: 3-bar high lookback before entry candle.
* **Stop-Loss Price**:
  $$\text{SL} = \max\left(\text{SwingHigh} \times (1 + \text{SWING\_SL\_BUFFER\_PCT}), \ \text{Entry} \times (1 + \text{MIN\_SL\_BUFFER\_PCT})\right)$$
  *(where $\text{SWING\_SL\_BUFFER\_PCT} = 0.05\%$ and $\text{MIN\_SL\_BUFFER\_PCT} = 0.20\%$)*
* **Risk (R)**:
  $$R = \text{SL} - \text{Entry}$$
* **Profit Target (1:2 R:R)**:
  $$\text{Target (TP)} = \text{Entry} - (2 \times R)$$

### 3. Dynamic +1R Trailing Stop Loss to Breakeven
* During live monitoring, if price drops to $+1R$ profit ($\text{Low} \le \text{Entry} - R$):
  $$\text{New SL} = \text{Entry Price} \quad (\text{Breakeven / ₹0 Capital Risk})$$

### 4. Mandatory 3:00 PM Auto-Squareoff
* At 15:00 IST, any remaining open positions are immediately exited at the current market tick price to avoid overnight exposure and broker auto-squareoff penalties.
