"""
System-Wide Configuration & Quantitative Parameters.

Centralized source of truth across all modules (data pipelines,
strategies, backtesting simulations, and live execution daemons).
"""

import os
from dataclasses import dataclass
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class TradingConfig:
    # Active Broker Default (shoonya | zerodha | dhan | groww | angelone | upstox | fyers | zero)
    ACTIVE_BROKER: str = "shoonya"

    # Portfolio Capital & Allocation Defaults
    INITIAL_CAPITAL: float = 10000.0
    MAX_CONCURRENT_POSITIONS: int = 2
    LEVERAGE_MIS: int = 5

    # Market Data & Timeframe Settings
    TIMEFRAME: str = "15m"
    BACKTEST_PERIOD: str = "60d"

    # Strategy Entry Timing Windows (IST)
    ENTRY_START_HOUR: int = 10
    ENTRY_START_MINUTE: int = 0
    ENTRY_END_HOUR: int = 13
    ENTRY_END_MINUTE: int = 30

    # Intraday Auto-Squareoff Timing (IST)
    SQUAREOFF_HOUR: int = 15
    SQUAREOFF_MINUTE: int = 0

    # Execution Mode Defaults
    TRADING_MODE: str = "paper"  # Execution sandbox: "paper" (paper_trades.db) | "live" (live_trades.db)
    ORDER_TYPE: str = "BO"       # Order type: "BO" (Bracket Order with exchange SL) | "MIS" (Margin Intraday Square-off)

    # Notification & Alert Channels: list of enabled mediums (e.g. () for none, ("telegram",), ("telegram", "discord", "email"))
    ALERT_CHANNELS: tuple = ("telegram",)

    # Active Position Guardian High-Frequency Polling (Seconds)
    POSITION_MONITOR_INTERVAL_SEC: int = 15  # Poll active positions every 15s for instant SL/TP triggers

    # Risk Management Rules
    MIN_SL_BUFFER_PCT: float = 0.0020      # 0.2% min SL buffer above entry
    SWING_SL_BUFFER_PCT: float = 0.0005    # 0.05% anti-wick buffer above swing high
    SWING_HIGH_BARS: int = 3               # 3-bar swing high lookback
    RISK_REWARD_RATIO: float = 2.0         # 1:2 R:R target
    MAX_DAILY_LOSS_PCT: float = 0.04        # 4% Max Daily Portfolio Loss Circuit Breaker
    MAX_RISK_PER_TRADE_PCT: float = 0.01    # 1% Max Portfolio Risk Per Trade (Issue #24)

    # Computed Properties
    @property
    def per_trade_margin(self) -> float:
        """Cash margin allocated per open position slot."""
        return self.INITIAL_CAPITAL / self.MAX_CONCURRENT_POSITIONS

    @property
    def per_trade_exposure(self) -> float:
        """Total purchasing power / exposure per trade using intraday MIS leverage."""
        return self.per_trade_margin * self.LEVERAGE_MIS


# Global Default Singleton Instance
CONFIG = TradingConfig()
