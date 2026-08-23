"""
core/market_calendar.py
Multi-Exchange Market Calendar & Timing Domain Logic.

Provides pure domain calculations for:
  - Multi-market timezones and session open/close boundaries (NSE, MCX, CDS, US_EQUITY, CRYPTO)
  - Strategy entry windows derived from universal relative offsets (warmup/cutoff)
  - Mandatory intraday auto-squareoff times
  - Real-time market status checks (is_market_open, is_market_closed, is_entry_window_active)
  - Pre-market and next-session countdowns with automated weekend/holiday skipping
"""

import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Dict, Any
from config import EXCHANGE_PROFILES, TradingConfig, CONFIG

# -------------------------------------------------------------------------
# Platform-Wide Market Timing Constants & Fail-Safes
# -------------------------------------------------------------------------
PLATFORM_HARD_CUTOFF_MINUTES_BEFORE_CLOSE: int = 15


def get_market_profile(market_key: str = "NSE") -> Dict[str, Any]:
    """Retrieves the declarative dictionary profile for a given market key."""
    norm_key = (market_key or "NSE").upper()
    return EXCHANGE_PROFILES.get(norm_key, EXCHANGE_PROFILES["NSE"])


def get_market_timezone(market_key: str = "NSE") -> ZoneInfo:
    """Returns the ZoneInfo timezone object for the requested market."""
    prof = get_market_profile(market_key)
    return ZoneInfo(prof.get("timezone", "Asia/Kolkata"))


def is_continuous_market(market_key: str = "NSE") -> bool:
    """Returns True if the market operates 24/7 without session boundaries (e.g. CRYPTO)."""
    return bool(get_market_profile(market_key).get("is_continuous", False))


def get_market_open_close(market_key: str = "NSE") -> Tuple[Optional[datetime.time], Optional[datetime.time]]:
    """
    Returns (open_time, close_time) in the market's local timezone.
    Returns (None, None) for continuous 24/7 markets.
    """
    prof = get_market_profile(market_key)
    if prof.get("is_continuous"):
        return None, None

    o_h, o_m = prof["open"]
    c_h, c_m = prof["close"]
    return datetime.time(o_h, o_m), datetime.time(c_h, c_m)


def get_strategy_entry_window(
    market_key: str = "NSE",
    warmup_minutes: int = 45,
    cutoff_minutes: int = 120
) -> Tuple[Optional[datetime.time], Optional[datetime.time]]:
    """
    Calculates dynamic strategy entry start and end times using relative offsets:
      - entry_start = market_open + warmup_minutes
      - entry_end   = market_close - cutoff_minutes
    Returns (None, None) for continuous 24/7 markets (always open).
    """
    if is_continuous_market(market_key):
        return None, None

    open_time, close_time = get_market_open_close(market_key)
    if open_time is None or close_time is None:
        return None, None

    dummy_date = datetime.date(2026, 1, 1)
    open_dt = datetime.datetime.combine(dummy_date, open_time) + datetime.timedelta(minutes=warmup_minutes)
    close_dt = datetime.datetime.combine(dummy_date, close_time) - datetime.timedelta(minutes=cutoff_minutes)
    return open_dt.time(), close_dt.time()


def get_platform_hard_squareoff_time(market_key: str = "NSE") -> Optional[datetime.time]:
    """
    Calculates the exchange-specific platform-level hard fail-safe cutoff:
    fail_safe = market_close - 15 minutes.
    Returns None for 24/7 continuous crypto markets.
    """
    if is_continuous_market(market_key):
        return None
    _, close_time = get_market_open_close(market_key)
    if close_time is None:
        return None

    dummy_date = datetime.date(2026, 1, 1)
    sq_dt = datetime.datetime.combine(dummy_date, close_time) - datetime.timedelta(minutes=PLATFORM_HARD_CUTOFF_MINUTES_BEFORE_CLOSE)
    return sq_dt.time()


def get_squareoff_time(market_key: str = "NSE") -> Optional[datetime.time]:
    """
    Returns the mandatory intraday auto-squareoff time in exchange local timezone.
    Returns None for continuous 24/7 markets.
    """
    prof = get_market_profile(market_key)
    if prof.get("is_continuous") or not prof.get("squareoff"):
        return None
    sq_h, sq_m = prof["squareoff"]
    return datetime.time(sq_h, sq_m)


def get_localized_now(market_key: str = "NSE", now: Optional[datetime.datetime] = None) -> datetime.datetime:
    """Returns the current datetime converted to the market's local timezone."""
    tz = get_market_timezone(market_key)
    if now is None:
        return datetime.datetime.now(tz)
    if now.tzinfo is None:
        # Attach market timezone to naive datetime
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def is_market_open(market_key: str = "NSE", now: Optional[datetime.datetime] = None) -> bool:
    """
    Checks if the given time is within the market's official trading session.
    Automatically handles weekend closures for traditional exchanges and returns True for Crypto.
    """
    if is_continuous_market(market_key):
        return True

    local_dt = get_localized_now(market_key, now)
    if local_dt.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    open_time, close_time = get_market_open_close(market_key)
    if open_time is None or close_time is None:
        return True

    cur_time = local_dt.time()
    return open_time <= cur_time <= close_time


def is_market_closed(market_key: str = "NSE", now: Optional[datetime.datetime] = None) -> bool:
    """
    Checks if today's market session has concluded (post-close or weekend).
    Always returns False for continuous 24/7 markets.
    """
    if is_continuous_market(market_key):
        return False

    local_dt = get_localized_now(market_key, now)
    if local_dt.weekday() >= 5:
        return True

    _, close_time = get_market_open_close(market_key)
    if close_time is None:
        return False

    return local_dt.time() >= close_time


def is_entry_window_active(
    market_key: str = "NSE",
    warmup_minutes: int = 45,
    cutoff_minutes: int = 120,
    now: Optional[datetime.datetime] = None
) -> bool:
    """
    Checks if current time is within the strategy entry window.
    Always returns True for continuous 24/7 markets.
    """
    if is_continuous_market(market_key):
        return True

    local_dt = get_localized_now(market_key, now)
    if local_dt.weekday() >= 5:
        return False

    start_time, end_time = get_strategy_entry_window(market_key, warmup_minutes, cutoff_minutes)
    if start_time is None or end_time is None:
        return True

    return start_time <= local_dt.time() <= end_time


def is_squareoff_time(market_key: str = "NSE", now: Optional[datetime.datetime] = None) -> bool:
    """
    Checks if the mandatory day-end auto-squareoff threshold has been reached.
    Always returns False for continuous 24/7 markets.
    """
    if is_continuous_market(market_key):
        return False

    local_dt = get_localized_now(market_key, now)
    if local_dt.weekday() >= 5:
        return False

    sq_time = get_squareoff_time(market_key)
    if sq_time is None:
        return False

    return local_dt.time() >= sq_time


def get_seconds_until_market_open(market_key: str = "NSE", now: Optional[datetime.datetime] = None) -> int:
    """
    Calculates seconds remaining until market open today.
    Returns 0 for continuous 24/7 markets.
    """
    if is_continuous_market(market_key):
        return 0

    local_dt = get_localized_now(market_key, now)
    open_time, _ = get_market_open_close(market_key)
    if open_time is None:
        return 0

    open_dt = local_dt.replace(
        hour=open_time.hour,
        minute=open_time.minute,
        second=2,
        microsecond=0
    )
    delta_sec = int((open_dt - local_dt).total_seconds())
    return max(delta_sec, 2)


def get_seconds_until_entry_window(
    market_key: str = "NSE",
    warmup_minutes: int = 45,
    now: Optional[datetime.datetime] = None
) -> int:
    """
    Calculates seconds remaining until strategy entry window opens today.
    Returns 0 for continuous 24/7 markets.
    """
    if is_continuous_market(market_key):
        return 0

    local_dt = get_localized_now(market_key, now)
    start_time, _ = get_strategy_entry_window(market_key, warmup_minutes=warmup_minutes)
    if start_time is None:
        return 0

    entry_dt = local_dt.replace(
        hour=start_time.hour,
        minute=start_time.minute,
        second=3,
        microsecond=0
    )
    delta_sec = int((entry_dt - local_dt).total_seconds())
    return max(delta_sec, 2)


def get_next_market_session(market_key: str = "NSE", now: Optional[datetime.datetime] = None) -> Tuple[datetime.datetime, int]:
    """
    Calculates upcoming trading session open datetime and seconds remaining.
    Skips weekends and post-session hours.
    Returns (now, 0) for 24/7 continuous markets.
    """
    local_dt = get_localized_now(market_key, now)
    if is_continuous_market(market_key):
        return local_dt, 0

    open_time, _ = get_market_open_close(market_key)
    if open_time is None:
        return local_dt, 0

    candidate = local_dt.replace(
        hour=open_time.hour,
        minute=open_time.minute,
        second=0,
        microsecond=0
    )

    if local_dt >= candidate:
        candidate += datetime.timedelta(days=1)

    while candidate.weekday() >= 5:  # Skip Saturday & Sunday
        candidate += datetime.timedelta(days=1)

    delta_sec = int((candidate - local_dt).total_seconds())
    return candidate, max(delta_sec, 5)
