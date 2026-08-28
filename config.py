"""
Centralized Configuration Module for Algorithmic Trading Framework.

Defines:
  - Exchange market session profiles (NSE, BSE, MCX, CDS, US_EQUITY, CRYPTO)
  - Strategy parameters & relative warmup/cutoff offsets
  - Dual-Guard risk budgeting & position sizing thresholds
  - Isolated database paths for Paper vs Live modes
"""

import os
from dataclasses import dataclass
from typing import Dict, Any, Tuple
from dotenv import load_dotenv

load_dotenv()


# -------------------------------------------------------------------------
# Declarative Exchange Market Session Profiles
# -------------------------------------------------------------------------
EXCHANGE_PROFILES: Dict[str, Dict[str, Any]] = {
    "NSE": {
        "name": "National Stock Exchange of India (Equity)",
        "timezone": "Asia/Kolkata",
        "open": (9, 15),
        "close": (15, 30),
        "squareoff": (15, 0),
        "is_continuous": False,
    },
    "BSE": {
        "name": "Bombay Stock Exchange (Equity)",
        "timezone": "Asia/Kolkata",
        "open": (9, 15),
        "close": (15, 30),
        "squareoff": (15, 0),
        "is_continuous": False,
    },
    "MCX": {
        "name": "Multi Commodity Exchange of India",
        "timezone": "Asia/Kolkata",
        "open": (9, 0),
        "close": (23, 30),
        "squareoff": (23, 15),
        "is_continuous": False,
    },
    "CDS": {
        "name": "NSE/BSE Currency Derivatives",
        "timezone": "Asia/Kolkata",
        "open": (9, 0),
        "close": (17, 0),
        "squareoff": (16, 45),
        "is_continuous": False,
    },
    "US_EQUITY": {
        "name": "US Stock Markets (NYSE/NASDAQ)",
        "timezone": "America/New_York",
        "open": (9, 30),
        "close": (16, 0),
        "squareoff": (15, 45),
        "is_continuous": False,
    },
    "CRYPTO": {
        "name": "24/7 Cryptocurrency Market",
        "timezone": "UTC",
        "open": None,
        "close": None,
        "squareoff": None,
        "is_continuous": True,
    },
}


@dataclass(frozen=True)
class TradingConfig:
    # -------------------------------------------------------------------------
    # Target Market & Environment
    # -------------------------------------------------------------------------
    EXCHANGE_MARKET: str = "NSE"
    ACTIVE_BROKER: str = "shoonya"
    TRADING_MODE: str = "paper"     # 'paper' or 'live'
    UNIVERSE: str = "NIFTY200"       # 'NIFTY50' (50 stocks) or 'NIFTY200' (200 stocks)
    ORDER_TYPE: str = "BO"          # 'BO' or 'MIS'

    # -------------------------------------------------------------------------
    # OpenAlgo Unified OMS Gateway
    # -------------------------------------------------------------------------
    OPENALGO_HOST: str = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000")
    OPENALGO_WS_URL: str = os.getenv("OPENALGO_WS_URL", "ws://127.0.0.1:8765")
    OPENALGO_API_KEY: str = os.getenv("OPENALGO_API_KEY", "")


    # -------------------------------------------------------------------------
    # Capital & Portfolio Risk Sizing
    # -------------------------------------------------------------------------
    INITIAL_CAPITAL: float = 100000.0
    MAX_CONCURRENT_POSITIONS: int = 2
    LEVERAGE_MIS: int = 5
    MAX_RISK_PER_TRADE_PCT: float = 0.01  # 1% fixed capital risk per trade
    MAX_DAILY_LOSS_PCT: float = 0.04      # 4% max daily portfolio loss circuit breaker


    # -------------------------------------------------------------------------
    # Alerts & Guardian Polling
    # -------------------------------------------------------------------------
    ALERT_CHANNELS: tuple = ("telegram",)
    POSITION_MONITOR_INTERVAL_SEC: int = 15


# Default Global Configuration Instance
CONFIG = TradingConfig()
