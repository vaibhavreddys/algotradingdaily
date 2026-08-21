"""
Core Trade Persistence & Journaling Database Module.

Provides SQLite persistence layer for algorithmic trading:
  - Physical database isolation between Paper Trading (database/paper_trades.db)
    and Live Real-Money Trading (database/live_trades.db).
  - 2-Table Schema:
      1. active_positions: Open position tracking & crash recovery state.
      2. trade_history: Permanent trading journal storing completed trade PnL & fees.
  - Safe self-test probe with guaranteed zero-pollution cleanup.
"""

import os
import sys
import sqlite3
import datetime
from contextlib import contextmanager
from typing import List, Dict, Any, Optional, Iterator

# Ensure workspace root is in sys.path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from config import CONFIG, TradingConfig

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_DIR = os.path.join(BASE_DIR, "database")


class TradeExitReason:
    """Standardized uppercase database enum identifiers for trade closure outcomes."""
    TARGET_HIT = "TARGET_HIT"                            # 1:2 R:R target achieved
    SL_HIT = "SL_HIT"                                    # Initial Stop Loss triggered
    TRAILING_SL_HIT = "TRAILING_SL_HIT"                  # Trailed SL (Breakeven) triggered
    ALGO_SQUAREOFF_DAY_END = "ALGO_SQUAREOFF_DAY_END"    # 3:00 PM session square-off
    DAILY_LOSS_THRESHOLD_HIT = "DAILY_LOSS_THRESHOLD_HIT"# Daily max portfolio loss reached
    MANUAL_SQUAREOFF = "MANUAL_SQUAREOFF"                # User manual emergency square-off
    BROKER_RMS_SQUAREOFF = "BROKER_RMS_SQUAREOFF"        # Broker RMS forced exit


EXIT_DISPLAY_LABELS = {
    TradeExitReason.TARGET_HIT: "TARGET HIT ✅",
    TradeExitReason.SL_HIT: "SL HIT ❌",
    TradeExitReason.TRAILING_SL_HIT: "TRAIL SL (BE) 🛡️",
    TradeExitReason.ALGO_SQUAREOFF_DAY_END: "3PM EXIT ⏱️",
    TradeExitReason.DAILY_LOSS_THRESHOLD_HIT: "DAILY MAX LOSS EXIT 🚨",
    TradeExitReason.MANUAL_SQUAREOFF: "MANUAL EXIT 🛑",
    TradeExitReason.BROKER_RMS_SQUAREOFF: "BROKER RMS EXIT ⚠️",
}


def get_db_path(mode: Optional[str] = None) -> str:
    """Returns absolute path to the SQLite database file for the given mode (defaults to CONFIG.TRADING_MODE)."""
    selected_mode = (mode or CONFIG.TRADING_MODE).lower()
    os.makedirs(DB_DIR, exist_ok=True)
    if selected_mode == "live":
        return os.path.join(DB_DIR, "live_trades.db")
    return os.path.join(DB_DIR, "paper_trades.db")


@contextmanager
def get_db_connection(mode: Optional[str] = None) -> Iterator[sqlite3.Connection]:
    """
    Creates and yields an SQLite database connection with concurrency hardening:
      - WAL mode: Enables concurrent readers without blocking writers.
      - busy_timeout: Automatically waits up to 5000ms on lock contention.
      - synchronous: NORMAL mode for optimal performance in WAL mode.
    Guarantees explicit connection close on context exit.
    """
    db_path = get_db_path(mode)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA busy_timeout = 5000;")
    cursor.execute("PRAGMA synchronous = NORMAL;")
    try:
        yield conn
    finally:
        conn.close()


def init_db(mode: Optional[str] = None) -> None:
    """
    Initializes the 2-table schema for the specified mode if not already present.
    """
    with get_db_connection(mode) as conn:
        cursor = conn.cursor()
        
        # 1. Active Positions Table (for live monitoring & crash recovery)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_positions (
                symbol TEXT PRIMARY KEY,
                order_type TEXT DEFAULT 'BO',
                entry_order_id TEXT,
                sl_order_id TEXT,
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                initial_sl REAL NOT NULL,
                current_sl REAL NOT NULL,
                target_price REAL NOT NULL,
                status TEXT DEFAULT 'ACTIVE',
                entry_time TEXT NOT NULL
            )
        """)
        
        # 2. Trade History Table (Permanent closed-trade journal)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                order_type TEXT DEFAULT 'BO',
                entry_time TEXT NOT NULL,
                exit_time TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                result TEXT NOT NULL,
                gross_pnl REAL NOT NULL,
                taxes_fees REAL NOT NULL,
                net_pnl REAL NOT NULL,
                balance_after_trade REAL,
                created_at TEXT NOT NULL
            )
        """)

        # 3. Schema Auto-Migration: Add balance_after_trade if missing
        cursor.execute("PRAGMA table_info(trade_history);")
        columns = [col[1] for col in cursor.fetchall()]
        if "balance_after_trade" not in columns:
            cursor.execute("ALTER TABLE trade_history ADD COLUMN balance_after_trade REAL DEFAULT NULL;")
            cursor.execute("SELECT id, net_pnl FROM trade_history ORDER BY id ASC;")
            existing_rows = cursor.fetchall()
            running_bal = CONFIG.INITIAL_CAPITAL
            for r_id, pnl in existing_rows:
                running_bal += float(pnl)
                cursor.execute("UPDATE trade_history SET balance_after_trade = ? WHERE id = ?", (round(running_bal, 2), r_id))

        # 3. Performance Composite B-Tree Indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_exit_symbol 
            ON trade_history(exit_time, symbol);
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_entry_time 
            ON trade_history(entry_time);
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_active_symbol 
            ON active_positions(symbol, status);
        """)
        conn.commit()


def save_active_position(
    symbol: str,
    entry_order_id: Optional[str],
    sl_order_id: Optional[str],
    qty: int,
    entry_p: float,
    sl_p: float,
    tp_p: float,
    order_type: str = "BO",
    mode: str = "paper"
) -> None:
    """Persists a newly opened position in active_positions table with 2-decimal precision."""
    init_db(mode)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with get_db_connection(mode) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO active_positions 
            (symbol, order_type, entry_order_id, sl_order_id, quantity, entry_price, initial_sl, current_sl, target_price, status, entry_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
        """, (
            symbol, order_type, str(entry_order_id or ""), str(sl_order_id or ""),
            int(qty), round(float(entry_p), 2), round(float(sl_p), 2), round(float(sl_p), 2), round(float(tp_p), 2), now_str
        ))
        conn.commit()


def update_trailing_sl(symbol: str, new_sl_price: float, mode: str = "paper") -> bool:
    """Updates Stop Loss level when trailing to breakeven."""
    init_db(mode)
    with get_db_connection(mode) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE active_positions 
            SET current_sl = ?, status = 'TRAILING'
            WHERE symbol = ?
        """, (round(float(new_sl_price), 2), symbol))
        conn.commit()
        return cursor.rowcount > 0


def close_and_archive_position(
    symbol: str,
    exit_price: float,
    exit_time: str,
    result: str,
    gross_pnl: float,
    taxes_fees: float,
    net_pnl: float,
    mode: str = "paper"
) -> bool:
    """
    Atomically closes an active position and inserts the completed trade
    into the trade_history journal with 2-decimal precision.
    """
    init_db(mode)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with get_db_connection(mode) as conn:
        cursor = conn.cursor()
        
        # 1. Fetch active position details
        cursor.execute("SELECT * FROM active_positions WHERE symbol = ?", (symbol,))
        pos = cursor.fetchone()
        
        if not pos:
            return False
            
        pos_dict = dict(pos)
        
        # 2. Fetch previous balance_after_trade to maintain atomic ledger
        cursor.execute("SELECT balance_after_trade FROM trade_history ORDER BY id DESC LIMIT 1;")
        last_row = cursor.fetchone()
        if last_row and last_row[0] is not None:
            prev_balance = float(last_row[0])
        else:
            cursor.execute("SELECT SUM(net_pnl) FROM trade_history;")
            cum_sum = cursor.fetchone()[0]
            prev_balance = CONFIG.INITIAL_CAPITAL + (float(cum_sum) if cum_sum is not None else 0.0)

        new_balance = round(prev_balance + float(net_pnl), 2)

        # 3. Insert into permanent trade_history
        cursor.execute("""
            INSERT INTO trade_history 
            (symbol, order_type, entry_time, exit_time, entry_price, exit_price, quantity, result, gross_pnl, taxes_fees, net_pnl, balance_after_trade, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol,
            pos_dict.get("order_type", "BO"),
            pos_dict.get("entry_time", now_str),
            exit_time,
            round(float(pos_dict.get("entry_price", 0.0)), 2),
            round(float(exit_price), 2),
            int(pos_dict.get("quantity", 1)),
            result,
            round(float(gross_pnl), 2),
            round(float(taxes_fees), 2),
            round(float(net_pnl), 2),
            new_balance,
            now_str
        ))
        
        # 3. Delete from active_positions
        cursor.execute("DELETE FROM active_positions WHERE symbol = ?", (symbol,))
        conn.commit()
        return True


def get_active_positions(mode: str = "paper") -> List[Dict[str, Any]]:
    """Retrieves all open trades on startup or health check."""
    init_db(mode)
    with get_db_connection(mode) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM active_positions WHERE status != 'CLOSED'")
        return [dict(row) for row in cursor.fetchall()]


def get_trade_journal(mode: str = "paper", limit: int = 100) -> List[Dict[str, Any]]:
    """Retrieves completed trade records from trade_history."""
    init_db(mode)
    with get_db_connection(mode) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trade_history ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]


def get_stale_positions(mode: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Inspects active_positions in either 'paper' or 'live' DB (defaults to CONFIG.TRADING_MODE)
    and returns unclosed trades from previous calendar days without modifying any data.
    Calculates elapsed age for diagnostics.
    """
    selected_mode = (mode or CONFIG.TRADING_MODE).lower()
    active_list = get_active_positions(mode=selected_mode)
    now = datetime.datetime.now()
    today_start = datetime.datetime(now.year, now.month, now.day, 0, 0, 0)
    
    stale_list = []
    for pos in active_list:
        entry_time_str = pos.get('entry_time', '')
        try:
            entry_dt = datetime.datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                entry_dt = datetime.datetime.fromisoformat(entry_time_str)
            except Exception:
                entry_dt = None

        if entry_dt and entry_dt < today_start:
            elapsed = now - entry_dt
            days = elapsed.days
            hours, remainder = divmod(elapsed.seconds, 3600)
            mins, _ = divmod(remainder, 60)
            
            if days > 0:
                age_str = f"{days}d {hours}h"
            elif hours > 0:
                age_str = f"{hours}h {mins}m"
            else:
                age_str = f"{mins}m"
                
            pos_copy = dict(pos)
            pos_copy['age_str'] = age_str
            pos_copy['elapsed_seconds'] = int(elapsed.total_seconds())
            stale_list.append(pos_copy)
            
    return stale_list


def reconcile_stale_positions(mode: str = "paper") -> List[Dict[str, Any]]:
    """
    Automated pre-market self-healing reconciler (Issue #15):
    Inspects active_positions for past-session orphaned trades, replays their candle
    history via simulate_single_trade() to discover the exact historical exit
    (TARGET_HIT, SL_HIT, TRAILING_SL_HIT, or ALGO_SQUAREOFF_DAY_END), calculates
    statutory charges/PnL, and atomically archives them to trade_history.
    """
    from core.charges import calculate_charges
    from data_pipeline.data_feed import load_candle_data
    from strategies.vwap_stoch_breakdown import simulate_single_trade

    stale_trades = get_stale_positions(mode=mode)
    if not stale_trades:
        return []

    reconciled_records = []
    init_db(mode)

    for trade in stale_trades:
        symbol = trade['symbol']
        ticker_yf = symbol.replace('-EQ', '') + '.NS' if not symbol.endswith('.NS') else symbol
        entry_time_str = trade['entry_time']
        entry_price = float(trade['entry_price'])
        quantity = int(trade['quantity'])
        order_type = trade.get('order_type', 'BO')

        # 1. Attempt historical candle replay
        resolved_exit_price = entry_price
        resolved_exit_time = entry_time_str
        resolved_result = TradeExitReason.ALGO_SQUAREOFF_DAY_END

        try:
            raw_df = load_candle_data(ticker_yf, period="60d", interval=CONFIG.TIMEFRAME, force_refresh=False, verbose=False)
            if raw_df is not None and not raw_df.empty:
                # Find entry row
                entry_dt = None
                try:
                    entry_dt = datetime.datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    try:
                        entry_dt = datetime.datetime.fromisoformat(entry_time_str)
                    except Exception:
                        pass

                if entry_dt:
                    entry_date = entry_dt.date()
                    day_candles = raw_df[raw_df.index.date == entry_date]
                    if not day_candles.empty:
                        # Locate entry candle index within full raw_df
                        matching_indices = [i for i, dt in enumerate(raw_df.index) if dt.date() == entry_date and dt <= entry_dt]
                        entry_idx = matching_indices[-1] if matching_indices else None
                        
                        if entry_idx is not None:
                            sim_result = simulate_single_trade(raw_df, entry_idx, ticker_yf, config=CONFIG)
                            if sim_result:
                                resolved_exit_price = round(float(sim_result['Exit Price']), 2)
                                resolved_exit_time = sim_result['Exit Time'].strftime("%Y-%m-%d %H:%M:%S")
                                resolved_result = sim_result['Result']
                            else:
                                # Default to 3:00 PM candle close of that day
                                sq_candles = day_candles[(day_candles.index.hour == CONFIG.SQUAREOFF_HOUR) & (day_candles.index.minute >= CONFIG.SQUAREOFF_MINUTE)]
                                if not sq_candles.empty:
                                    resolved_exit_price = round(float(sq_candles.iloc[0]['Close']), 2)
                                    resolved_exit_time = sq_candles.index[0].strftime("%Y-%m-%d %H:%M:%S")
                                else:
                                    resolved_exit_price = round(float(day_candles.iloc[-1]['Close']), 2)
                                    resolved_exit_time = day_candles.index[-1].strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            # Fallback to trade SL or entry price if data replay fails
            resolved_exit_price = round(float(trade.get('current_sl', entry_price)), 2)

        # 2. Compute financial charges & PnL
        # Short trade: Entry is SELL, Exit is BUY
        entry_turnover = entry_price * quantity
        exit_turnover = resolved_exit_price * quantity
        gross_pnl = round(entry_turnover - exit_turnover, 2)
        taxes_fees = round(calculate_charges(
            sell_turnover=entry_turnover,
            buy_turnover=exit_turnover,
            broker=getattr(CONFIG, 'ACTIVE_BROKER', 'shoonya')
        ), 2)
        net_pnl = round(gross_pnl - taxes_fees, 2)

        # 3. Atomically archive to trade_history and clear from active_positions
        created_at_now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db_connection(mode=mode) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trade_history (
                    symbol, order_type, entry_time, exit_time, 
                    entry_price, exit_price, quantity, result, 
                    gross_pnl, taxes_fees, net_pnl, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, order_type, entry_time_str, resolved_exit_time,
                entry_price, resolved_exit_price, quantity, resolved_result,
                gross_pnl, taxes_fees, net_pnl, created_at_now
            ))
            cursor.execute("DELETE FROM active_positions WHERE symbol = ?", (symbol,))
            conn.commit()

        reconciled_records.append({
            'symbol': symbol,
            'entry_time': entry_time_str,
            'exit_time': resolved_exit_time,
            'entry_price': entry_price,
            'exit_price': resolved_exit_price,
            'quantity': quantity,
            'result': resolved_result,
            'net_pnl': net_pnl
        })

    return reconciled_records


if __name__ == "__main__":
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    print("\n=======================================================")
    print("       TRADE DATABASE MODULE (core/trade_db.py)")
    print("=======================================================")

    init_db("paper")
    init_db("live")

    paper_stale = get_stale_positions(mode="paper")
    live_stale = get_stale_positions(mode="live")
    total_stale = len(paper_stale) + len(live_stale)

    # 1. Prominent Stale / Orphan Positions Diagnostic Header
    print("--- [1/2] STALE / ORPHAN POSITION AUDIT ---------------")
    if total_stale == 0:
        print("  ✅ Status: CLEAN — 0 orphan/stale positions across all databases.")
    else:
        print(f"  ⚠️ Status: ATTENTION — Found {total_stale} stale position(s) from past sessions!")
        if paper_stale:
            print("  📂 Paper Trading DB Stale Positions:")
            for p in paper_stale:
                print(f"     • {p['symbol']:<12} | Entry: {p['entry_time']} | Qty: {p['quantity']:>3} | Price: ₹{p['entry_price']:>8,.2f} | Age: {p['age_str']}")
        if live_stale:
            print("  📂 Live Real-Money DB Stale Positions:")
            for p in live_stale:
                print(f"     • {p['symbol']:<12} | Entry: {p['entry_time']} | Qty: {p['quantity']:>3} | Price: ₹{p['entry_price']:>8,.2f} | Age: {p['age_str']}")

    # 2. Database Health & Persistence State
    print("\n--- [2/2] DATABASE STORAGE & RECORD COUNTS ------------")
    paper_active = len(get_active_positions(mode="paper"))
    paper_history = len(get_trade_journal(mode="paper", limit=10000))
    live_active = len(get_active_positions(mode="live"))
    live_history = len(get_trade_journal(mode="live", limit=10000))

    print(f"  [1] Paper Trading DB  : {get_db_path('paper')}")
    print(f"      Active Open Slots : {paper_active} (Stale: {len(paper_stale)})")
    print(f"      Completed Trades  : {paper_history}")
    print(f"  [2] Live Real-Money DB: {get_db_path('live')}")
    print(f"      Active Open Slots : {live_active} (Stale: {len(live_stale)})")
    print(f"      Completed Trades  : {live_history}")
    print("-------------------------------------------------------")
    print("STATUS: ✅ Both SQLite databases initialized and ready (WAL Mode & 5000ms Busy Timeout Enabled).")
    print("TIP   : Run 'python -m unittest tests/test_trade_db.py' for full test suite.")
    print("=======================================================\n")


def get_today_realized_pnl(mode: str = "paper") -> float:
    """
    Queries the SQLite trade_history table and returns the sum of today's realized net PnL in INR.
    """
    init_db(mode)
    today_prefix = datetime.datetime.now().strftime("%Y-%m-%d")
    with get_db_connection(mode) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT SUM(net_pnl) FROM trade_history WHERE exit_time LIKE ?;",
            (f"{today_prefix}%",)
        )
        row = cursor.fetchone()
        if row and row[0] is not None:
            return float(row[0])
    return 0.0
