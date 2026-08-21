"""
Base Live Trading Execution Daemon & Order Management Framework.

Provides shared runtime capabilities:
  - Automated TOTP authentication for Shoonya API
  - Synchronized market clock & 15-minute candle interval scheduler
  - Position lifecycle & trailing stop-loss state machine (+1R -> Breakeven)
  - 15:00 IST auto-squareoff enforcement
"""

import os
import sys
import datetime
# pyrefly: ignore [missing-import]
import pyotp
from typing import Dict, Any, List, Optional
# pyrefly: ignore [missing-import]
from NorenRestApiPy.NorenApi import NorenApi

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from config import CONFIG, TradingConfig
from core.capital import get_persisted_paper_capital
from core.risk import is_daily_loss_limit_reached
from core.trade_db import get_today_realized_pnl
from data_pipeline import get_nifty50_symbols


class BaseTradingEngine(NorenApi):
    """
    Base trading engine containing shared authentication, market clock,
    position tracking, and risk management logic.
    """
    def __init__(self, config: TradingConfig = CONFIG):
        super().__init__(
            host='https://api.shoonya.com/NorenWSTScript/', 
            websocket='wss://api.shoonya.com/NorenWSTScript/'
        )
        self.config = config
        self.active_positions: Dict[str, Dict[str, Any]] = {}

    def is_daily_circuit_breaker_active(self) -> bool:
        """
        Checks if today's cumulative realized losses meet or exceed the 3% max daily loss limit.
        """
        today_pnl = get_today_realized_pnl(mode=self.config.TRADING_MODE)
        start_cap = self.get_account_capital()
        return is_daily_loss_limit_reached(
            today_realized_pnl=today_pnl,
            day_starting_capital=start_cap,
            max_loss_pct=self.config.MAX_DAILY_LOSS_PCT
        )

    def get_account_capital(self) -> float:
        """
        Returns the current active account capital:
          - Live Mode: Queries live Shoonya broker funds API (api.get_limits()).
          - Paper Mode: Reconstructs cumulative balance from SQLite paper_trades.db.
        """
        if self.config.TRADING_MODE == "live" and self.api:
            try:
                limits = self.api.get_limits()
                if limits and limits.get('stat') == 'Ok':
                    cash = float(limits.get('cash', 0.0))
                    payin = float(limits.get('payin', 0.0))
                    marginused = float(limits.get('marginused', 0.0))
                    available = cash + payin - marginused
                    if available > 0:
                        return available
            except Exception as e:
                print(f"⚠️ Failed to fetch live broker limits ({e}). Falling back to default.")
        return get_persisted_paper_capital(initial_capital=self.config.INITIAL_CAPITAL, mode=self.config.TRADING_MODE)

        self.cached_nifty_benchmark: Optional[Any] = None
        self.user = os.getenv("SHOONYA_USER")
        self.pwd = os.getenv("SHOONYA_PWD")
        self.api_key = os.getenv("SHOONYA_API_KEY")
        self.vendor_code = os.getenv("SHOONYA_VENDOR_CODE")
        self.totp_key = os.getenv("SHOONYA_TOTP_KEY")
        self.imei = os.getenv("SHOONYA_IMEI", "shoonya_algo_desktop")

    def prewarm_benchmark_feed(self) -> bool:
        """
        Pre-fetches NIFTY 50 benchmark index ~5s before candle boundary into in-memory RAM
        to eliminate network latency on candle close.
        """
        from data_pipeline import fetch_nifty_benchmark
        try:
            feed = fetch_nifty_benchmark(period="5d", interval=self.config.TIMEFRAME)
            if feed is not None and not feed.empty:
                self.cached_nifty_benchmark = feed
                return True
            return False
        except Exception:
            return False

    def get_benchmark_feed(self) -> Any:
        """Returns pre-warmed benchmark feed or performs on-demand fallback fetch."""
        from data_pipeline import fetch_nifty_benchmark
        if self.cached_nifty_benchmark is not None and not self.cached_nifty_benchmark.empty:
            feed = self.cached_nifty_benchmark
            self.cached_nifty_benchmark = None  # Consume cache
            return feed
        return fetch_nifty_benchmark(period="5d", interval=self.config.TIMEFRAME)

    def authenticate(self) -> bool:
        """Performs automated TOTP authentication with Shoonya or falls back to virtual mode."""
        is_placeholder = (
            not self.user or not self.totp_key or 
            "your_" in (self.user or "").lower() or 
            "your_" in (self.totp_key or "").lower()
        )
        if is_placeholder:
            print("⚠️ Shoonya credentials not configured. Running in Offline Virtual Mode (yfinance feed).")
            return False

        try:
            totp = pyotp.TOTP(self.totp_key).now()
            res = self.login(
                userid=self.user, password=self.pwd, twoFA=totp,
                vendor_code=self.vendor_code, api_secret=self.api_key, imei=self.imei
            )
            if res and res.get('stat') == 'Ok':
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ Authenticated to Shoonya API.")
                return True
            print("❌ Authentication Failed:", res)
            return False
        except Exception as e:
            print(f"⚠️ Shoonya Auth Exception ({e}). Running in Offline Virtual Mode.")
            return False

    def is_market_open(self, now: Optional[datetime.datetime] = None) -> bool:
        """Checks if current time is within official NSE trading session (09:15 to 15:30 IST on weekdays)."""
        now = now or datetime.datetime.now()
        if now.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        t = now.time()
        market_open = datetime.time(9, 15)
        market_close = datetime.time(15, 30)
        return market_open <= t <= market_close

    def is_market_closed(self, now: Optional[datetime.datetime] = None) -> bool:
        """Checks if today's session has completely concluded (past 15:30 IST or weekend)."""
        now = now or datetime.datetime.now()
        if now.weekday() >= 5:
            return True
        return now.time() >= datetime.time(15, 30)

    def is_entry_window_active(self, now: Optional[datetime.datetime] = None) -> bool:
        """Checks if current time is within allowed entry window (10:00 AM to 1:30 PM)."""
        now = now or datetime.datetime.now()
        t = now.time()
        start = datetime.time(self.config.ENTRY_START_HOUR, self.config.ENTRY_START_MINUTE)
        end = datetime.time(self.config.ENTRY_END_HOUR, self.config.ENTRY_END_MINUTE)
        return start <= t <= end

    def render_filter_funnel(
        self,
        eval_count: int,
        total_symbols: int,
        rel_weak_count: int,
        vwap_count: int,
        adx_count: int,
        stoch_count: int,
        signals_fired: int
    ) -> None:
        """
        Renders an itemized ASCII filter funnel breakdown after evaluating the trading universe.
        """
        open_slots = len(self.active_positions)
        max_slots = self.config.MAX_CONCURRENT_POSITIONS
        now_str = datetime.datetime.now().strftime("%H:%M:%S")

        print(f"[{now_str}] 📊 15m Scan Funnel ({eval_count}/{total_symbols} constituents evaluated):")
        print(f"    • Relative Weakness vs NIFTY : {rel_weak_count:>2d}/{total_symbols} stocks")
        print(f"    • Price < Intraday VWAP       : {vwap_count:>2d}/{total_symbols} stocks")
        print(f"    • Strong ADX Trend (ADX > 25) : {adx_count:>2d}/{total_symbols} stocks")
        print(f"    • Stochastic RSI Breakdown    : {stoch_count:>2d}/{total_symbols} stocks")
        print(f"    ---------------------------------------------")
        print(f"    ⭐ Qualified Entries Fired    : {signals_fired:>2d} trade(s) | Open Slots: {open_slots}/{max_slots}")

    def is_squareoff_time(self, now: Optional[datetime.datetime] = None) -> bool:
        """Checks if current time is past mandatory square-off time (3:00 PM)."""
        now = now or datetime.datetime.now()
        t = now.time()
        sq_time = datetime.time(self.config.SQUAREOFF_HOUR, self.config.SQUAREOFF_MINUTE)
        return t >= sq_time

    def get_seconds_until_next_candle(self, interval_mins: int = 15, now: Optional[datetime.datetime] = None) -> int:
        """
        Calculates exact seconds remaining until the next 15-minute candle boundary (:00, :15, :30, :45).
        Adds a small 3-second buffer to guarantee the candle bar has officially closed.
        """
        now = now or datetime.datetime.now()
        current_minute = now.minute
        current_second = now.second
        
        minutes_into_interval = current_minute % interval_mins
        minutes_remaining = interval_mins - minutes_into_interval - 1
        seconds_remaining = (minutes_remaining * 60) + (60 - current_second) + 3
        return max(seconds_remaining, 5)

    def get_trading_universe(self) -> List[str]:
        """Returns symbols formatted for Shoonya NSE cash trading (e.g. INFY-EQ)."""
        symbols = get_nifty50_symbols()
        return [f"{s.replace('.NS', '')}-EQ" for s in symbols]

    def sync_active_positions_from_db(self, mode: Optional[str] = None) -> int:
        """Restores open trade state from SQLite database on engine startup/recovery and resolves past-session stale trades."""
        from core.trade_db import get_active_positions, get_stale_positions, reconcile_stale_positions, get_db_path
        
        target_mode = (mode or self.config.TRADING_MODE).lower()
        db_path = get_db_path(target_mode)
        
        # 1. Startup Sanity Diagnostics & Automated Reconciler (Issue #15)
        stale_positions = get_stale_positions(mode=target_mode)
        if stale_positions:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⚠️ Detected {len(stale_positions)} stale position(s) from past session(s). Starting automated reconciliation...")
            reconciled = reconcile_stale_positions(mode=target_mode)
            for r in reconciled:
                print(f"    ✅ Reconciled: {r['symbol']} | Exited @ ₹{r['exit_price']} ({r['exit_time']}) | Result: {r['result']} | Net PnL: ₹{r['net_pnl']:+,.2f}")
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🛡️ Pre-market reconciliation complete: All stale positions archived.")
        else:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ Database Sanity Check: 0 stale positions detected in {db_path}.")

        # 2. Restore active positions into in-memory state
        self.active_positions.clear()
        saved = get_active_positions(mode=target_mode)
        for pos in saved:
            self.active_positions[pos['symbol']] = pos
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔄 State synchronized: {len(self.active_positions)} active position(s) loaded from DB.")
        return len(self.active_positions)
