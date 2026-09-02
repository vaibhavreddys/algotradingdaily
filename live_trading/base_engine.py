"""
Base Live Trading & Risk Management Architecture.

Provides the universal execution daemon, risk guardian, and scheduler:
  - Multi-threaded universe scanner on 15m candle closes (:00, :15, :30, :45)
  - Pre-warming benchmark relative weakness feeds (~5s before close)
  - Micro 15-second guardian loop for active positions & +1R Trailing SL to Breakeven
  - Automated market calendar checks, holiday handling, and pre-market sleep
  - Daily 3% circuit breaker enforcement & 15:00 mandatory squareoff
  - End-Of-Day (EOD) reporting & automated past-session stale trade reconciliation
"""

import os
import sys
import time
import datetime
from typing import Dict, Any, Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

# Ensure workspace root is in sys.path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from config import CONFIG, TradingConfig
from core.market_calendar import is_market_open as is_mc_open, is_market_closed as is_mc_closed, get_next_market_session as get_mc_next_session, get_seconds_until_market_open as get_mc_sec_open, get_seconds_until_entry_window as get_mc_sec_entry, is_entry_window_active as is_mc_entry_active, is_squareoff_time as is_mc_sqoff
from core.capital import calculate_order_quantity, get_persisted_paper_capital
from core.risk import is_daily_loss_limit_reached
from core.trade_db import (
    TradeExitReason,
    EXIT_DISPLAY_LABELS,
    save_active_position,
    update_trailing_sl,
    close_and_archive_position,
    get_active_positions,
    get_stale_positions,
    reconcile_stale_positions,
    get_db_path,
    get_trade_journal,
)
from alerts import notify_trade_entry, notify_trailing_sl, notify_trade_exit, notify_eod_summary, notify_system_error
from strategies.registry import load_strategy_instance, discover_strategies
from data_pipeline import (
    get_nifty50_symbols,
    fetch_nifty_benchmark,
    fetch_verified_candles,
    fetch_latest_tick_price,
)

try:
    from openalgo import api as OpenAlgoClient
except ImportError:
    class OpenAlgoClient:
        def __init__(self, *args, **kwargs): pass
        def funds(self): return {}
        def margin(self): return {}
        def quotes(self, *args, **kwargs): return {}
        def get_ltp(self, *args, **kwargs): return {}
        def history(self, *args, **kwargs): return {}
        def placeorder(self, *args, **kwargs): return {}
        def modifyorder(self, *args, **kwargs): return {}
        def cancelorder(self, *args, **kwargs): return {}
        def positionbook(self, *args, **kwargs): return {}


from contextlib import contextmanager


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_AWAYMODE_REQUIRED = 0x00000040

@contextmanager
def prevent_sleep_context():
    """
    Context manager that requests Windows OS execution state to stay awake.
    On non-Windows platforms (Linux VPS), gracefully no-ops.
    """
    import sys
    if sys.platform != 'win32':
        yield
        return

    try:
        import ctypes
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
        if hasattr(ctypes, 'windll') and hasattr(ctypes.windll, 'kernel32'):
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        yield
    finally:
        try:
            import ctypes
            if hasattr(ctypes, 'windll') and hasattr(ctypes.windll, 'kernel32'):
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass


class BaseTradingEngine:
    """
    Universal Foundation for Strategy Execution, Risk Control, and Broker Communication.
    """
    def __init__(self, config: TradingConfig = CONFIG, mode: str = "paper", strategy_name: Optional[str] = None, strategy_version: Optional[str] = None):
        self.config = config
        self.mode = mode.lower()

        # Dynamic Strategy Loading from Registry
        strat_name = strategy_name or getattr(config, 'ACTIVE_STRATEGY', 'vwap_stoch_trend')
        strat_ver = strategy_version or getattr(config, 'ACTIVE_STRATEGY_VERSION', 'v1_2')
        try:
            self.strategy = load_strategy_instance(strat_name, strat_ver)
            self.strategy_name = getattr(self.strategy, 'NAME', strat_name)
            self.strategy_version = getattr(self.strategy, 'VERSION', strat_ver)
            self.timeframe = getattr(self.strategy, 'TIMEFRAME', '15m')
            self.swing_bars = getattr(self.strategy, 'SWING_BARS', getattr(self.strategy, 'SWING_HIGH_BARS', 3))
        except Exception:
            from strategies.vwap_stoch_trend.v1_2 import STRATEGY_INSTANCE
            self.strategy = STRATEGY_INSTANCE
            self.strategy_name = self.strategy.NAME
            self.strategy_version = self.strategy.VERSION
            self.timeframe = getattr(self.strategy, 'TIMEFRAME', '15m')
            self.swing_bars = 3

        self.api = None
        self.authenticated = False
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        self.closed_trades: List[Dict[str, Any]] = []
        self._cached_benchmark: Optional[pd.Series] = None
        self._benchmark_timestamp: Optional[datetime.datetime] = None

        self.day_starting_capital = self.get_account_capital()

    def is_daily_circuit_breaker_active(self) -> bool:
        """Checks if daily realized drawdowns have reached the daily safety threshold."""
        curr_cap = self.get_account_capital()
        today_realized_pnl = curr_cap - self.day_starting_capital
        return is_daily_loss_limit_reached(
            today_realized_pnl=today_realized_pnl,
            day_starting_capital=self.day_starting_capital,
            max_loss_pct=self.config.MAX_DAILY_LOSS_PCT
        )

    @staticmethod
    def _looks_like_auth_failure(payload: Any) -> Optional[str]:
        """
        Heuristic that returns a human-readable reason if a broker/OpenAlgo
        response indicates an expired or invalid session. Returns ``None`` for
        unrelated errors so callers can keep their existing flow.
        """
        if payload is None:
            return None
        if isinstance(payload, dict):
            for key in ("message", "msg", "error", "reason"):
                val = payload.get(key)
                if isinstance(val, str) and (
                    "session" in val.lower()
                    and ("expir" in val.lower() or "invalid" in val.lower())
                ):
                    return val
        text = str(payload)
        lowered = text.lower()
        if "401" in text or "unauthorized" in lowered:
            return text
        if "session" in lowered and ("expir" in lowered or "invalid" in lowered):
            return text
        return None

    def get_account_capital(self) -> float:
        """Returns the active available capital for position sizing."""
        if (self.mode == "live" or getattr(self.config, 'TRADING_MODE', 'paper') == "live") and self.api:
            try:
                limits = self.api.get_limits()
                auth_reason = self._looks_like_auth_failure(limits)
                if auth_reason:
                    notify_system_error(
                        component="OpenAlgo",
                        error_msg=f"Broker session rejected: {auth_reason}",
                        severity="warning",
                        action_taken="Please run the morning re-authentication script to refresh the broker API key.",
                    )
                if limits and limits.get('stat') == 'Ok':
                    # Check payin / cash / net fields
                    cash = float(limits.get('cash', 0.0))
                    margin_used = float(limits.get('marginused', 0.0))
                    payin = float(limits.get('payin', 0.0))
                    net_avail = (cash + payin) - margin_used
                    if net_avail > 0:
                        return net_avail
                    if 'net' in limits:
                        return float(limits['net'])
            except Exception:
                pass
            return float(self.config.INITIAL_CAPITAL)
        return get_persisted_paper_capital(initial_capital=self.config.INITIAL_CAPITAL, mode=self.mode)

    def prewarm_benchmark_feed(self) -> bool:
        """Pre-warms the benchmark feed ~5s before candle close to eliminate scanning latency."""
        try:
            feed = fetch_nifty_benchmark(period="5d", interval=self.timeframe, force_refresh=True)
            if feed is not None and not feed.empty:
                self._cached_benchmark = feed
                self._benchmark_timestamp = datetime.datetime.now()
                return True
        except Exception:
            pass
        return False

    def get_benchmark_feed(self) -> Any:
        """Returns the latest benchmark series for relative weakness filtering."""
        now = datetime.datetime.now()
        if (
            self._cached_benchmark is not None
            and self._benchmark_timestamp is not None
            and (now - self._benchmark_timestamp).total_seconds() < 120
        ):
            return self._cached_benchmark

        feed = fetch_nifty_benchmark(period="5d", interval=self.timeframe, force_refresh=True)
        self._cached_benchmark = feed
        self._benchmark_timestamp = now
        return feed

    def authenticate(self) -> bool:
        """Connects to OpenAlgo Unified Broker Gateway or initializes virtual paper mode."""
        if self.mode == "paper" and not getattr(self.config, 'OPENALGO_API_KEY', None):
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🛡️ [PAPER MODE] Virtual execution active (Zero capital risk).")
            self.authenticated = True
            return True

        host = getattr(self.config, 'OPENALGO_HOST', 'http://127.0.0.1:5000')
        api_key = getattr(self.config, 'OPENALGO_API_KEY', '') or os.getenv('OPENALGO_API_KEY', '')

        try:
            client = OpenAlgoClient(api_key=api_key, host=host)
            self.api = client
            self.authenticated = True
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ Connected to OpenAlgo Unified OMS Gateway ({host}).")
            return True
        except Exception as e:
            err_text = str(e).strip() or repr(e)
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⚠️ OpenAlgo connection failed: {e}. Running in local fallback.")
            notify_system_error(
                component="OpenAlgo",
                error_msg=f"Gateway unreachable at {host}: {err_text}",
                severity="critical",
                action_taken="Engine fell back to yfinance market feed for this session.",
            )
            self.authenticated = True
            return True

    def get_seconds_until_market_open(self, now: Optional[datetime.datetime] = None) -> int:
        return get_mc_sec_open(getattr(self.config, 'EXCHANGE', 'NSE'), now=now)

    def get_seconds_until_entry_window(self, now: Optional[datetime.datetime] = None) -> int:
        return get_mc_sec_entry(getattr(self.config, 'EXCHANGE', 'NSE'), now=now)

    def get_next_market_session(self, now: Optional[datetime.datetime] = None) -> Tuple[datetime.datetime, int]:
        return get_mc_next_session(getattr(self.config, 'EXCHANGE', 'NSE'), now=now)

    def is_market_open(self, now: Optional[datetime.datetime] = None) -> bool:
        return is_mc_open(getattr(self.config, 'EXCHANGE', 'NSE'), now=now)

    def is_market_closed(self, now: Optional[datetime.datetime] = None) -> bool:
        return is_mc_closed(getattr(self.config, 'EXCHANGE', 'NSE'), now=now)

    def is_entry_window_active(self, now: Optional[datetime.datetime] = None) -> bool:
        return is_mc_entry_active(getattr(self.config, 'EXCHANGE', 'NSE'), now=now)

    def is_squareoff_time(self, now: Optional[datetime.datetime] = None) -> bool:
        return is_mc_sqoff(getattr(self.config, 'EXCHANGE', 'NSE'), now=now)

    def get_seconds_until_next_candle(self, interval_mins: int = 15, now: Optional[datetime.datetime] = None) -> int:
        now = now or datetime.datetime.now()
        curr_min = now.minute
        curr_sec = now.second
        remainder = curr_min % interval_mins
        wait_mins = interval_mins - remainder if remainder != 0 else (interval_mins if curr_sec > 5 else 0)
        wait_secs = (wait_mins * 60) - curr_sec + 2
        return max(1, wait_secs)

    def sync_active_positions_from_db(self, mode: Optional[str] = None) -> int:
        """Restores open trade state from SQLite database on engine startup/recovery."""
        target_mode = (mode or self.mode).lower()
        
        # Pre-market automated calendar reconciler
        stale_positions = get_stale_positions(mode=target_mode)
        if stale_positions:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⚠️ Detected {len(stale_positions)} stale position(s) from past session(s). Starting automated reconciliation...")
            reconciled = reconcile_stale_positions(mode=target_mode)
            for r in reconciled:
                print(f"    ✓ Reconciled: {r['symbol']} | Exited @ ₹{r['exit_price']} ({r['exit_time']}) | Result: {r['result']} | Net PnL: ₹{r['net_pnl']:+,.2f}")
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚀 Pre-market reconciliation complete: All stale positions archived.")

        active_db_rows = get_active_positions(mode=target_mode)
        self.active_positions.clear()
        
        # Check live broker positions (via self.get_positions or self.api.get_positions)
        broker_positions_map = {}
        if target_mode == "live":
            try:
                get_pos_fn = getattr(self, 'get_positions', None) or (self.api.get_positions if self.api else None)
                if get_pos_fn:
                    b_pos_list = get_pos_fn()
                    if b_pos_list and isinstance(b_pos_list, list):
                        for bp in b_pos_list:
                            tsym = bp.get('tsym')
                            netqty = int(bp.get('netqty', 0))
                            if tsym:
                                broker_positions_map[tsym] = netqty
                                clean = tsym.replace('-EQ', '').replace('.NS', '')
                                broker_positions_map[clean] = netqty
            except Exception:
                pass

        for row in active_db_rows:
            sym = row['symbol']
            clean_s = sym.replace('.NS', '').replace('-EQ', '')
            if broker_positions_map:
                found_zero = False
                for b_sym, b_qty in broker_positions_map.items():
                    b_clean = b_sym.replace('.NS', '').replace('-EQ', '')
                    if b_clean == clean_s and b_qty == 0:
                        found_zero = True
                        break
                if found_zero:
                    close_and_archive_position(
                        symbol=sym,
                        exit_price=row['entry_price'],
                        exit_time=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        result=TradeExitReason.ALGO_SQUAREOFF_DAY_END,
                        gross_pnl=0.0,
                        taxes_fees=0.0,
                        net_pnl=0.0,
                        mode="live"
                    )
                    continue

            self.active_positions[sym] = {
                'symbol': sym,
                'entry_price': row['entry_price'],
                'entry_time': row.get('entry_time'),
                'qty': row['quantity'],
                'sl_price': row['current_sl'],
                'tp_price': row['target_price'],
                'risk': abs(row['current_sl'] - row['entry_price']),
                'trailed': (row['current_sl'] <= row['entry_price']),
                'entry_order_id': row.get('entry_order_id'),
                'sl_order_id': row.get('sl_order_id'),
            }
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 📂 Restored {len(self.active_positions)} active position(s) from {get_db_path(target_mode)}.")
        return len(self.active_positions)

    def _evaluate_single_symbol(self, ticker: str, nifty_pct_map: pd.Series) -> Optional[Dict[str, Any]]:
        """Worker function to fetch and evaluate strategy signals for a single symbol."""
        try:
            raw_df = fetch_verified_candles(
                ticker,
                period="60d",
                interval=self.timeframe,
                api_client=self.api
            )
            if raw_df is None or len(raw_df) < (self.swing_bars + 5):
                return None

            df = self.strategy.evaluate_signals(raw_df, nifty_pct_map, config=self.config)
            if df is None or len(df) == 0:
                return None

            last_idx = len(df) - 1
            last_row = df.iloc[last_idx]
            swing_high = float(df.iloc[last_idx - self.swing_bars : last_idx]['High'].max()) if last_idx >= self.swing_bars else 0.0
            direction = str(last_row.get('Direction', 'SHORT'))

            sl_p, tp_p, risk_amt = self.strategy.calculate_stop_and_target(
                df,
                entry_idx=last_idx,
                direction=direction
            )

            return {
                'ticker': ticker,
                'sym_key': f"{ticker.replace('.NS', '')}-EQ",
                'last_row': last_row,
                'raw_df': raw_df,
                'swing_high': swing_high,
                'direction': direction,
                'sl_price': sl_p,
                'tp_price': tp_p,
                'signal': bool(last_row.get('Signal', False)),
                'rel_weak_pass': bool(last_row.get('Rel_Weakness_Pass', False)),
                'vwap_pass': bool(last_row.get('VWAP_Pass', False)),
                'adx_pass': bool(last_row.get('ADX_Pass', False)),
                'stoch_pass': bool(last_row.get('Stoch_Pass', False)),
            }
        except Exception:
            return None


    def render_filter_funnel(
        self,
        eval_count: int = 50,
        total_symbols: int = 50,
        rel_weak_count: int = 0,
        vwap_count: int = 0,
        adx_count: int = 0,
        stoch_count: int = 0,
        signals_fired: int = 0,
        **kwargs
    ) -> str:
        """Renders formatted funnel telemetry string."""
        open_slots = max(0, self.config.MAX_CONCURRENT_POSITIONS - len(self.active_positions) - signals_fired)
        lines = [
            f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 📊 15m Scan Funnel ({eval_count}/{total_symbols} constituents evaluated):",
            f"  • Relative Weakness vs NIFTY : {rel_weak_count:>2}/{total_symbols} stocks",
            f"  • Price < Intraday VWAP       : {vwap_count:>2}/{total_symbols} stocks",
            f"  • Strong ADX Trend (ADX > 25) : {adx_count:>2}/{total_symbols} stocks",
            f"  • Stochastic RSI Breakdown    : {stoch_count:>2}/{total_symbols} stocks",
            f"  • Qualified Entries Fired    : {signals_fired:>2} trade(s) | Open Slots: {kwargs.get('open_slots', len(self.active_positions))}/{self.config.MAX_CONCURRENT_POSITIONS}"
        ]
        msg = "\n".join(lines)
        print(msg)
        return msg

    def scan_and_execute_signals(self, nifty_pct_map: pd.Series) -> None:
        """Scans the universe across worker threads on 15m candle close boundaries."""
        if self.is_daily_circuit_breaker_active():
            today_realized_pnl = self.get_account_capital() - self.day_starting_capital
            loss_pct = (
                (today_realized_pnl / self.day_starting_capital) * 100.0
                if self.day_starting_capital > 0 else 0.0
            )
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🛑 [CIRCUIT BREAKER] Daily Loss reached. Halting new scan entries.")
            notify_system_error(
                component="CircuitBreaker",
                error_msg=(
                    f"Daily loss cap of {self.config.MAX_DAILY_LOSS_PCT * 100:.1f}% reached "
                    f"(realized PnL: ₹{today_realized_pnl:+,.2f} / {loss_pct:+.2f}%)."
                ),
                severity="halt",
                action_taken=(
                    "All open positions were squared off and new entries are blocked for the remainder of the session."
                ),
            )
            return

        universe_name = getattr(self.config, 'UNIVERSE', 'NIFTY50').upper()
        from data_pipeline.data_feed import get_symbols_for_universe
        symbols = get_symbols_for_universe(universe_name)
        total_symbols = len(symbols)

        funnel_stats = {
            'total_universe': total_symbols,
            'rel_weakness_passed': 0,
            'vwap_passed': 0,
            'adx_passed': 0,
            'stoch_passed': 0,
            'final_signals': 0
        }

        candidates = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(self._evaluate_single_symbol, t, nifty_pct_map) for t in symbols]
            for f in futures:
                try:
                    res = f.result()
                except Exception as scan_err:
                    import traceback
                    notify_system_error(
                        component="EngineLoop.15mScanner",
                        error_msg=(
                            f"Unhandled exception in scanner worker: {scan_err}\n"
                            f"{traceback.format_exc(limit=3).strip()}"
                        ),
                        severity="critical",
                        action_taken="Symbol skipped this cycle; engine will retry on the next 15m candle.",
                    )
                    continue
                if res is None:
                    continue
                if res['rel_weak_pass']: funnel_stats['rel_weakness_passed'] += 1
                if res['vwap_pass']: funnel_stats['vwap_passed'] += 1
                if res['adx_pass']: funnel_stats['adx_passed'] += 1
                if res['stoch_pass']: funnel_stats['stoch_passed'] += 1

                if res['signal']:
                    funnel_stats['final_signals'] += 1
                    candidates.append((
                        res['ticker'],
                        res['sym_key'],
                        res['last_row'],
                        res['sl_price'],
                        res['tp_price'],
                        res['direction']
                    ))

        # Count feed sources used during this scan
        broker_feeds = sum(1 for f in futures if f.result() and getattr(f.result().get('raw_df', None), '_data_source', '').startswith('Shoonya'))
        yf_feeds = total_symbols - broker_feeds
        feed_summary = f"Broker Gateway (OpenAlgo: {broker_feeds})" if broker_feeds > 0 else f"Fallback Network (yfinance: {yf_feeds})"

        # Print Funnel Telemetry
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 📊 15m Filter Funnel [Feed: {feed_summary}]: "
              f"Universe({funnel_stats['total_universe']}) -> "
              f"RelWeak({funnel_stats['rel_weakness_passed']}) -> "
              f"VWAP({funnel_stats['vwap_passed']}) -> "
              f"ADX({funnel_stats['adx_passed']}) -> "
              f"Stoch({funnel_stats['stoch_passed']}) -> "
              f"Signals({funnel_stats['final_signals']})")

        # Execute qualifying candidates if open slots exist
        for ticker, sym_key, row, sl_p, tp_p, direction in candidates:
            if len(self.active_positions) >= self.config.MAX_CONCURRENT_POSITIONS:
                break
            if sym_key in self.active_positions:
                continue

            entry_p = float(row['Close'])

            # Delegate order placement to child class hook. Wrap so any rejection
            # (raised exception or a False return from the child hook) is routed to
            # the operational error channel instead of being silently swallowed.
            try:
                placed = self.execute_entry(symbol=sym_key, entry_price=entry_p, sl_price=sl_p, tp_price=tp_p)
            except Exception as entry_err:
                notify_system_error(
                    component="OrderPlacement",
                    error_msg=f"Exception placing entry for {sym_key}: {entry_err}",
                    severity="rejection",
                    action_taken="Entry skipped; signal will be re-evaluated on the next 15m candle.",
                )
                continue
            if not placed:
                notify_system_error(
                    component="OrderPlacement",
                    error_msg=f"Broker rejected entry order for {sym_key} @ ₹{entry_p:,.2f} (SL ₹{sl_p:,.2f}).",
                    severity="rejection",
                    action_taken="Check broker console for margin / freeze-quantity reasons; entry skipped for this candle.",
                )

    def monitor_active_positions(self) -> None:
        """Micro 15-second guardian checking active positions for SL/TP hits & +1R Trailing SL."""
        if not self.active_positions:
            return

        for symbol in list(self.active_positions.keys()):
            ticker = f"{symbol.replace('-EQ', '')}.NS"
            try:
                tick = fetch_latest_tick_price(ticker, api_client=self.api)
                if tick is None:
                    continue
                ltp = tick['ltp']
                high = tick['high']
                low = tick['low']

                self.update_position(symbol=symbol, current_ltp=ltp, high=high, low=low)
            except Exception as mon_err:
                import traceback
                notify_system_error(
                    component="EngineLoop.15sGuardian",
                    error_msg=(
                        f"Exception while monitoring {symbol}: {mon_err}\n"
                        f"{traceback.format_exc(limit=3).strip()}"
                    ),
                    severity="critical",
                    action_taken="Position skipped this tick; guardian will retry on the next 15-second sweep.",
                )
                continue

    def squareoff_all_positions(self) -> None:
        """Mandatory 3:00 PM square-off for all remaining open positions."""
        if not self.active_positions:
            return

        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] MANDATORY 3:00 PM AUTO-SQUAREOFF ENFORCED.")
        from data_pipeline import fetch_latest_tick_price as data_tick_fn
        for symbol in list(self.active_positions.keys()):
            clean_t = symbol.replace('-EQ', '')
            ticker = f"{clean_t}.NS" if not clean_t.endswith('.NS') else clean_t
            ltp = self.active_positions[symbol]['entry_price']
            try:
                import live_trading.paper_trader
                tick_fn = getattr(live_trading.paper_trader, 'fetch_latest_tick_price', data_tick_fn)
                tick = tick_fn(ticker) or tick_fn(clean_t) or data_tick_fn(ticker, api_client=self.api)
                if tick is not None and tick.get('ltp'):
                    ltp = tick['ltp']
            except Exception:
                pass

            self.execute_squareoff(symbol=symbol, exit_price=ltp, reason=TradeExitReason.ALGO_SQUAREOFF_DAY_END)

    def generate_eod_report(self) -> None:
        """Generates End-Of-Day performance report from trade database."""
        today_date = datetime.datetime.now().strftime('%Y-%m-%d')
        all_trades = get_trade_journal(mode=self.mode, limit=500)
        day_trades = [t for t in all_trades if str(t.get('exit_time', '')).startswith(today_date)]

        from core.report import print_daily_eod_report
        ending_balance = self.get_account_capital()
        eod_msg, _ = print_daily_eod_report(
            day_trades=day_trades,
            initial_capital=self.config.INITIAL_CAPITAL,
            ending_balance=ending_balance,
            date_str=today_date
        )
        # Always dispatch EOD summary scorecard to Telegram (even on 0-trade discipline days)
        notify_eod_summary(report_text=eod_msg, mode=self.mode, config=self.config)

    def run(self) -> None:
        """Alias for run_live_loop() to provide standard execution interface."""
        self.run_live_loop()

    def run_live_loop(self) -> None:
        """Universal Macro/Micro live loop driver for both Paper and Live modes."""
        print(f"       ENGINE: {self.strategy_name} {self.strategy_version} ({self.mode.upper()} TRADING)")
        print(f"       Capital: Rs.{self.get_account_capital():,.0f} | Max Slots: {self.config.MAX_CONCURRENT_POSITIONS}")
        print(f"       Scanning: 15m Candle Closes | Guardian: {self.config.POSITION_MONITOR_INTERVAL_SEC}s Ticks")
        print("=======================================================")

        self.authenticate()
        self.sync_active_positions_from_db(mode=self.mode)

        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚀 Live {self.mode.upper()} Engine started.")

        try:
            while True:
                now = datetime.datetime.now()

                if now.weekday() >= 5 or now.time() >= datetime.time(15, 30):
                    next_sess, remaining_sec = self.get_next_market_session(now)
                    print(f"[{now.strftime('%H:%M:%S')}] 💤 Market is closed (Weekend / Post-Market).")
                    print(f"👉 Next session: {next_sess.strftime('%A %Y-%m-%d 09:15:00')} IST ({remaining_sec}s remaining).")
                    if self.active_positions:
                        self.squareoff_all_positions()
                    self.generate_eod_report()
                    break

                if not self.is_market_open(now):
                    wait_sec = self.get_seconds_until_market_open(now)
                    target_time = (now + datetime.timedelta(seconds=wait_sec)).strftime('%H:%M:%S')
                    print(f"[{now.strftime('%H:%M:%S')}] ⏳ Pre-market: Sleeping {wait_sec}s until market open at {target_time} IST...")
                    time.sleep(wait_sec)
                    continue

                if not self.is_entry_window_active(now) and not self.active_positions and now.time() < datetime.time(10, 0):
                    wait_sec = self.get_seconds_until_entry_window(now)
                    target_time = (now + datetime.timedelta(seconds=wait_sec)).strftime('%H:%M:%S')
                    print(f"[{now.strftime('%H:%M:%S')}] ⏳ Pre-entry: 0 active positions. Sleeping {wait_sec}s until entry window opens at {target_time} IST...")
                    time.sleep(wait_sec)
                    continue

                if self.is_squareoff_time(now):
                    if self.active_positions:
                        self.squareoff_all_positions()
                    self.generate_eod_report()
                    print(f"[{now.strftime('%H:%M:%S')}] ✅ Trading session completed for today.")
                    break

                if self.is_entry_window_active(now):
                    if len(self.active_positions) < self.config.MAX_CONCURRENT_POSITIONS:
                        nifty_pct_map = self.get_benchmark_feed()
                        self.scan_and_execute_signals(nifty_pct_map)

                wait_sec = self.get_seconds_until_next_candle(interval_mins=15, now=now)
                next_check = (now + datetime.timedelta(seconds=wait_sec)).strftime('%H:%M:%S')
                print(f"[{now.strftime('%H:%M:%S')}] ⏳ Next 15m scan in {wait_sec}s ({next_check}). Active slots: {len(self.active_positions)}/{self.config.MAX_CONCURRENT_POSITIONS}")

                poll_interval = self.config.POSITION_MONITOR_INTERVAL_SEC
                target_wake_time = time.time() + wait_sec
                prewarmed = False

                while time.time() < target_wake_time:
                    remaining_time = target_wake_time - time.time()
                    if remaining_time <= 5.0 and not prewarmed and self.is_entry_window_active(datetime.datetime.now()):
                        try:
                            self.prewarm_benchmark_feed()
                            prewarmed = True
                        except Exception:
                            pass

                    sleep_chunk = min(poll_interval, remaining_time)
                    if sleep_chunk > 0:
                        time.sleep(sleep_chunk)

                    if self.active_positions:
                        self.monitor_active_positions()

                    if self.is_squareoff_time(datetime.datetime.now()):
                        break

        except KeyboardInterrupt:
            print(f"[INTERRUPT] User interrupted {self.mode.upper()} engine (Ctrl+C). Generating report...")
            self.generate_eod_report()

    # --- Abstract Hooks to be implemented by child classes ---
    def execute_entry(self, symbol: str, entry_price: float, sl_price: float, tp_price: float) -> bool:
        raise NotImplementedError

    def update_position(self, symbol: str, current_ltp: float, high: float, low: float, now: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
        """
        Universal micro-guardian monitoring active position against live market ticks.
        Delegates exit and trailing actions to the engine's execution hooks.
        """
        if symbol not in self.active_positions:
            return None

        pos = self.active_positions[symbol]
        entry_p = pos['entry_price']
        curr_sl = pos['sl_price']
        tp = pos['tp_price']
        risk = pos['risk']

        # 1. Check Stop Loss Trigger (In Paper / Non-BO Live fallback)
        if high >= curr_sl and self.mode == "paper":
            result = TradeExitReason.TRAILING_SL_HIT if pos['trailed'] else TradeExitReason.SL_HIT
            return self.execute_squareoff(symbol, exit_price=curr_sl, reason=result)

        # 2. Check Target Trigger (In Paper / Non-BO Live fallback)
        if low <= tp and self.mode == "paper":
            return self.execute_squareoff(symbol, exit_price=tp, reason=TradeExitReason.TARGET_HIT)

        # 3. Universal +1R Trailing SL to Breakeven
        if not pos['trailed'] and low <= (entry_p - risk):
            self.execute_trailing_sl(symbol, be_price=entry_p)

        # 4. Mandatory 3:00 PM Squareoff
        if self.is_squareoff_time(now=now):
            return self.execute_squareoff(symbol, exit_price=current_ltp, reason=TradeExitReason.ALGO_SQUAREOFF_DAY_END)

        return None

    def execute_squareoff(self, symbol: str, exit_price: float, reason: str) -> bool:
        raise NotImplementedError
