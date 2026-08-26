"""
Telegram Subscribers Registry.

Owns the SQLite store of every chat_id that is allowed to receive trade alerts.
The store is shared by:
  - alerts.telegram.TelegramAlertChannel (broadcast lookup + dead-subscriber cleanup)
  - alerts.tg_bot                       (subscribe / unsubscribe / admin commands)

The DB is git-ignored at database/telegram_subscribers.db, same convention as
database/paper_trades.db and database/live_trades.db.
"""

import os
import sys
import sqlite3
import datetime
import hmac
from contextlib import contextmanager
from typing import List, Optional, Iterator

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_DIR = os.path.join(BASE_DIR, "database")
DB_PATH = os.path.join(DB_DIR, "telegram_subscribers.db")

MAX_INVITE_ATTEMPTS = 2  # 2 wrong codes -> banned


def get_db_path() -> str:
    """Returns the absolute path to the subscribers DB, creating database/ if needed.

    Honors the TELEGRAM_SUBSCRIBERS_DB_PATH override env var (used by tests
    to point at a tmp file). Falls back to database/telegram_subscribers.db.
    """
    override = os.getenv("TELEGRAM_SUBSCRIBERS_DB_PATH", "").strip()
    if override:
        os.makedirs(os.path.dirname(override) or ".", exist_ok=True)
        return override
    os.makedirs(DB_DIR, exist_ok=True)
    return DB_PATH


@contextmanager
def get_db_connection() -> Iterator[sqlite3.Connection]:
    """Opens a WAL-mode SQLite connection with a 5s busy timeout. Always closes on exit."""
    db_path = get_db_path()
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


def init_db() -> None:
    """Idempotent schema bootstrap. Safe to call on every process start."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                subscribed_at TEXT NOT NULL,
                last_seen TEXT,
                active INTEGER NOT NULL DEFAULT 0,
                banned INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'telegram',
                pending_code_attempts INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_subscribers_active ON subscribers(active);"
        )
        conn.commit()


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SubscribersRegistry:
    """
    Thin object wrapper around the subscribers SQLite store. Methods are
    small and synchronous; the underlying connection context manager
    handles the WAL/busy-timeout setup.
    """

    def __init__(self) -> None:
        init_db()

    # --- broadcast lookup --------------------------------------------------

    def active_chat_ids(self) -> List[int]:
        """Returns the list of chat_ids that should currently receive alerts."""
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT chat_id FROM subscribers WHERE active = 1 AND banned = 0 ORDER BY chat_id"
            ).fetchall()
        return [int(r["chat_id"]) for r in rows]

    def is_active(self, chat_id: int) -> bool:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT active, banned FROM subscribers WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        if row is None:
            return False
        return bool(row["active"]) and not bool(row["banned"])

    def is_banned(self, chat_id: int) -> bool:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT banned FROM subscribers WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return bool(row and row["banned"])

    # --- subscription lifecycle -------------------------------------------

    def start_invite(self, chat_id: int, username: Optional[str], first_name: Optional[str]) -> None:
        """Records (or refreshes) a chat_id that has begun the /start flow.

        Inserts a row with active=0 if absent, refreshes username/first_name/last_seen
        on every call so a returning user can re-attempt the invite code without
        needing to be re-invited by the owner.
        """
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO subscribers (chat_id, username, first_name, subscribed_at, last_seen, active, banned, source, pending_code_attempts)
                VALUES (?, ?, ?, ?, ?, 0, 0, 'telegram', 0)
                ON CONFLICT(chat_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_seen = excluded.last_seen,
                    pending_code_attempts = 0
                """,
                (chat_id, username, first_name, _now(), _now()),
            )
            conn.commit()

    def verify_invite(self, chat_id: int, code: str) -> bool:
        """Constant-time compares the code against TELEGRAM_INVITE_CODE.

        Returns True and activates the subscriber on match. On mismatch,
        increments pending_code_attempts and bans the chat at
        MAX_INVITE_ATTEMPTS wrong tries. Returns False otherwise.
        """
        expected = os.getenv("TELEGRAM_INVITE_CODE", "")
        match = bool(expected) and hmac.compare_digest(str(expected), str(code))

        with get_db_connection() as conn:
            if match:
                conn.execute(
                    """
                    UPDATE subscribers
                    SET active = 1,
                        banned = 0,
                        pending_code_attempts = 0,
                        subscribed_at = COALESCE(subscribed_at, ?),
                        last_seen = ?,
                        source = CASE WHEN source = 'env_seed' THEN source ELSE 'telegram' END
                    WHERE chat_id = ?
                    """,
                    (_now(), _now(), chat_id),
                )
                conn.commit()
                return True

            row = conn.execute(
                "SELECT pending_code_attempts, banned FROM subscribers WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            attempts = int(row["pending_code_attempts"]) if row else 0
            new_attempts = attempts + 1
            should_ban = (row is not None and bool(row["banned"])) or new_attempts >= MAX_INVITE_ATTEMPTS
            conn.execute(
                """
                UPDATE subscribers
                SET pending_code_attempts = ?,
                    banned = ?,
                    last_seen = ?
                WHERE chat_id = ?
                """,
                (new_attempts, 1 if should_ban else 0, _now(), chat_id),
            )
            conn.commit()
            return False

    def subscribe(
        self,
        chat_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        source: str = "telegram",
    ) -> None:
        """Activates a subscriber, clearing any prior ban flag."""
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO subscribers (chat_id, username, first_name, subscribed_at, last_seen, active, banned, source, pending_code_attempts)
                VALUES (?, ?, ?, ?, ?, 1, 0, ?, 0)
                ON CONFLICT(chat_id) DO UPDATE SET
                    username = COALESCE(excluded.username, subscribers.username),
                    first_name = COALESCE(excluded.first_name, subscribers.first_name),
                    active = 1,
                    banned = 0,
                    pending_code_attempts = 0,
                    last_seen = ?,
                    source = excluded.source
                """,
                (chat_id, username, first_name, _now(), _now(), source, _now()),
            )
            conn.commit()

    def unsubscribe(self, chat_id: int) -> bool:
        """Deactivates a subscriber. Returns True if a row was changed."""
        with get_db_connection() as conn:
            cursor = conn.execute(
                "UPDATE subscribers SET active = 0, last_seen = ? WHERE chat_id = ? AND active = 1",
                (_now(), chat_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_inactive(self, chat_id: int) -> None:
        """Called by the channel when Telegram returns 403/400 for a recipient."""
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE subscribers SET active = 0, last_seen = ? WHERE chat_id = ?",
                (_now(), chat_id),
            )
            conn.commit()

    def mark_banned(self, chat_id: int) -> bool:
        """Hard-ban a chat_id. Returns True if a row was updated."""
        with get_db_connection() as conn:
            cursor = conn.execute(
                "UPDATE subscribers SET banned = 1, active = 0, last_seen = ? WHERE chat_id = ?",
                (_now(), chat_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def reinstate(self, chat_id: int) -> bool:
        """Owner-only: clears ban, re-activates, marks source as admin_reinstate."""
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE subscribers
                SET active = 1, banned = 0, pending_code_attempts = 0,
                    last_seen = ?, source = 'admin_reinstate'
                WHERE chat_id = ?
                """,
                (_now(), chat_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    # --- admin / inspection ------------------------------------------------

    def list_active(self) -> List[sqlite3.Row]:
        with get_db_connection() as conn:
            return conn.execute(
                """
                SELECT chat_id, username, first_name, subscribed_at, source
                FROM subscribers
                WHERE active = 1 AND banned = 0
                ORDER BY subscribed_at
                """
            ).fetchall()

    def pending_chat_ids(self) -> List[int]:
        """chat_ids that have started /start but haven't successfully verified yet."""
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT chat_id FROM subscribers
                WHERE active = 0 AND banned = 0
                ORDER BY last_seen DESC
                """
            ).fetchall()
        return [int(r["chat_id"]) for r in rows]

    def touch(self, chat_id: int) -> None:
        """Updates last_seen for any user activity (does not flip active)."""
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE subscribers SET last_seen = ? WHERE chat_id = ?",
                (_now(), chat_id),
            )
            conn.commit()

    # --- env-var seed for backward compat ---------------------------------

    def seed_env_chat_id(self) -> bool:
        """
        If TELEGRAM_CHAT_ID is set, ensure that chat_id is in the subscribers
        table as an active entry (source='env_seed'). Idempotent. Returns True
        when a row is inserted (first run only).
        """
        seed = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not seed:
            return False
        try:
            seed_id = int(seed)
        except ValueError:
            return False
        with get_db_connection() as conn:
            existing = conn.execute(
                "SELECT chat_id FROM subscribers WHERE chat_id = ?", (seed_id,)
            ).fetchone()
            if existing is not None:
                # Make sure the seeded owner is active and not banned.
                conn.execute(
                    """
                    UPDATE subscribers
                    SET active = 1, banned = 0, last_seen = ?
                    WHERE chat_id = ?
                    """,
                    (_now(), seed_id),
                )
                conn.commit()
                return False
            conn.execute(
                """
                INSERT INTO subscribers (chat_id, username, first_name, subscribed_at, last_seen, active, banned, source, pending_code_attempts)
                VALUES (?, NULL, 'env_seed_owner', ?, ?, 1, 0, 'env_seed', 0)
                """,
                (seed_id, _now(), _now()),
            )
            conn.commit()
            return True
