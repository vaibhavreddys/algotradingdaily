# Strategy Specification: VWAP-Stochastic RSI Trend

## 1. Strategy Overview

| Parameter | Specification |
| :--- | :--- |
| **Strategy Name** | VWAP-Stochastic RSI Trend |
| **Active Version** | **v1.3.0 (Deterministic Confidence Ranking & Bidirectional)** |
| **Asset Class** | Indian Equities (NSE Cash MIS) |
| **Trade Direction** | **Bidirectional** (Long & Short with Macro NIFTY Filter) |
| **Trading Universe** | **NIFTY 200 Constituents** |
| **Primary Timeframe** | **15-Minute Candles** |
| **Max Concurrent Positions**| **2 Simultaneous Trades** (Dynamic Margin Compounding) |
| **Daily Loss Limit** | **4% Max Daily Loss Circuit Breaker** |
| **Risk per Trade** | **1% Fixed Account Risk (Dual-Guard Position Sizing)** |
| **Risk-to-Reward (R:R)** | **1:2 Dynamic Swing Target** with **+1R Trailing Breakeven Stop** |

---

## 2. Version Evolution Matrix

| Version | Direction | Universe | Stop Loss & Target | Candidate Slot Priority |
| :--- | :--- | :--- | :--- | :--- |
| **v1.0** | Short Only | NIFTY 50 | 5-Bar Swing High (1:2 R:R) | First Available Quote |
| **v1.1** | Short Only | NIFTY 50 | +1R Trailing Breakeven Stop | First Available Quote |
| **v1.2** | Bidirectional | NIFTY 200 | Long + Short (Macro NIFTY Trend Filter) | First Available Quote |
| **v1.3** | **Bidirectional** | **NIFTY 200** | **+1R Trailing SL + ATR Dynamic Clamping** | **Deterministic Confidence Score (0-100)** |

---

## 3. Quantitative Indicators & Mathematical Formulations

### A. Intraday VWAP (Volume Weighted Average Price)
Calculated continuously from market open (09:15 AM IST), resetting daily:
* Typical Price = (High + Low + Close) / 3
* VWAP = Sum(Typical Price * Volume) / Sum(Volume)

### B. Fast Stochastic RSI (%K)
Calculated using 14-period RSI and 14-period Stochastic smoothing:
* StochRSI = (RSI_14 - Min(RSI_14, 14)) / (Max(RSI_14, 14) - Min(RSI_14, 14))
* %K = SMA(StochRSI, 3) * 100

### C. Average Directional Index (ADX)
* Standard 14-period Wilder's smoothed ADX.
* **Trend Gate**: ADX >= 25 (Guarantees entries only in high-momentum conditions).

### D. Benchmark Relative Momentum
* Relative Spread = Stock Return % - NIFTY Return % (from market open).
* **Short Gate (Relative Weakness)**: Stock Return < NIFTY Return.
* **Long Gate (Relative Strength)**: Stock Return > NIFTY Return.

---

## 4. Confidence Scoring Engine (v1.3 Encapsulation)

When multiple stocks simultaneously qualify on the same 15-minute candle close, capital is allocated strictly to the highest-scoring candidate setups:

```text
Confidence Score (0 to 100) = 
    (0.40 * ADX_Norm) + 
    (0.35 * Rel_Momentum_Norm) + 
    (0.15 * VWAP_Displacement_Norm) + 
    (0.10 * Stoch_Hook_Norm)
```

1. **ADX Trend Conviction (40% Weight)**:
   * ADX_Norm = Clip((ADX - 25.0) / 25.0 * 100, 0, 100)
2. **Relative Momentum Spread (35% Weight)**:
   * Long: Clip((Stock Return - Nifty Return) / 0.025 * 100, 0, 100)
   * Short: Clip((Nifty Return - Stock Return) / 0.025 * 100, 0, 100)
3. **VWAP Displacement (15% Weight)**:
   * VWAP_Norm = Clip((|Close - VWAP| / VWAP) / 0.020 * 100, 0, 100)
4. **Stochastic RSI Hook Severity (10% Weight)**:
   * Long: Clip((Stoch_K - 20) / 20 * 100, 0, 100)
   * Short: Clip((80 - Stoch_K) / 20 * 100, 0, 100)

---

## 5. Execution Rules & Risk Management

1. **Entry Window**: Strictly **10:00 AM IST to 1:30 PM IST** (ignores opening whipsaws and avoids late-day slippage).
2. **Trailing Stop Loss (+1R Rule)**: When floating profit reaches +1R (equal to initial risk), Stop Loss moves to Breakeven (Entry Price).
3. **Mandatory 3:00 PM Squareoff**: All open intraday positions are squared off automatically at 3:00 PM IST before broker auto-squareoff charges apply.
4. **Daily Loss Circuit Breaker**: If total realized loss reaches 4% of starting daily capital, trading halts immediately for the rest of the day.
