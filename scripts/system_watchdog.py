#!/usr/bin/env python3
"""
Standalone Market-Hours Infrastructure Watchdog Daemon / Cron Runner.

Designed to be executed periodically (e.g. every 5 minutes via cron/systemd timer)
during market days on the VPS:
  1. Respects NSE market calendar (no-op on weekends, pre-market, post-market, and exchange holidays).
  2. Enforces SingletonLock("system_watchdog") so dual watchdogs never overlap.
  3. Diagnoses and auto-heals Gateway (port 5000), Shoonya Broker Auth, and Telegram Bot.
  4. Dispatches high-priority Telegram alert only when healing actions were required.
"""

import os
import sys
import datetime
import logging

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import CONFIG
from core.process_lock import SingletonLock
from core.market_calendar import is_market_open, is_market_closed
from core.system_recovery import auto_heal_all
from alerts import notify_system_error

logging.basicConfig(
    format="%(asctime)s [WATCHDOG] %(levelname)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("system_watchdog")


def main() -> int:
    now = datetime.datetime.now()

    # 1. Respect Market Calendar: no-op outside trading window
    # Watchdog is active from 08:45 AM (pre-market setup) to 15:35 PM (post-market EOD) on trading days
    if now.weekday() >= 5:
        log.info("Market closed (Weekend: %s). Watchdog no-op.", now.strftime("%A"))
        return 0

    exchange = getattr(CONFIG, "EXCHANGE", "NSE")
    if is_market_closed(exchange, now=now) and not (datetime.time(8, 45) <= now.time() <= datetime.time(15, 35)):
        log.info("Market closed (Outside 08:45-15:35 trading window). Watchdog no-op.")
        return 0

    # 2. Enforce Singleton Process Lock to prevent overlapping watchdog runs
    watchdog_lock = SingletonLock(service_name="system_watchdog", raise_on_conflict=False)
    if not watchdog_lock.acquire():
        locked_pid = watchdog_lock.get_locked_pid()
        pid_msg = f" (PID {locked_pid})" if locked_pid else ""
        log.warning("Another instance of system_watchdog is already running%s. Exiting cleanly.", pid_msg)
        return 0

    try:
        log.info("Running automated market-hours infrastructure diagnostic & self-healing...")
        report = auto_heal_all(CONFIG)

        actions = report.get("actions", [])
        final_diag = report.get("final", {})
        all_healthy = report.get("all_healed", False)

        if actions:
            action_summary = "\n".join(actions)
            log.info("Self-healing actions taken:\n%s", action_summary)
            # Dispatch informational alert to Telegram
            notify_system_error(
                component="SystemWatchdog",
                error_msg=f"Watchdog auto-healed degraded components:\n{action_summary}",
                severity="info" if all_healthy else "warning",
                action_taken="Auto-healers executed successfully.",
            )
        else:
            log.info("All 4 infrastructure pillars are healthy (Gateway, Broker, Engine, Resources).")

        return 0 if all_healthy else 1

    except Exception as exc:
        log.exception("Unexpected error in watchdog execution: %s", exc)
        return 1
    finally:
        watchdog_lock.release()


if __name__ == "__main__":
    sys.exit(main())
