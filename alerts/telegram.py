"""
Telegram Notification Channel Implementation.

Broadcasts every alert to the set of active subscribers stored in
`database/telegram_subscribers.db` (see alerts.subscribers). The
single-recipient `chat_id` constructor argument is preserved as a
deprecated no-op so existing call sites keep importing.
"""

import os
import threading
import requests
import datetime
import warnings
from typing import Optional, Iterable, Dict

from alerts.base import BaseAlertChannel
from alerts.subscribers import SubscribersRegistry

# Default cooldown for repeated operational-error alerts.
DEFAULT_SYSTEM_ERROR_COOLDOWN_SEC = 15 * 60

# Severity → emoji map. Unknown severities fall back to the warning glyph.
_SEVERITY_ICON = {
    "critical": "🚨",
    "warning": "⚠️",
    "halt": "🛑",
    "rejection": "❌",
}


class TelegramAlertChannel(BaseAlertChannel):
    """Dispatches formatted Markdown trade alerts to every active Telegram subscriber."""

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,  # deprecated; kept for backward-compat
    ):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        if chat_id is not None:
            warnings.warn(
                "TelegramAlertChannel(chat_id=...) is deprecated; the bot now "
                "broadcasts to all subscribers in database/telegram_subscribers.db.",
                DeprecationWarning,
                stacklevel=2,
            )
        self._registry = SubscribersRegistry()
        # Throttle state for operational-error alerts. Keyed by (component, error_msg);
        # values are UTC timestamps of the most recent dispatch.
        self._error_throttle: Dict[str, datetime.datetime] = {}
        self._error_throttle_lock = threading.Lock()
        self._error_cooldown_seconds = DEFAULT_SYSTEM_ERROR_COOLDOWN_SEC

    @property
    def is_configured(self) -> bool:
        return bool(self.token)

    # --- core broadcast ----------------------------------------------------

    def send_message(self, text: str) -> bool:
        """
        Sends `text` to every active subscriber. Returns True only if every
        recipient was delivered successfully. A 403 ("blocked by user") or
        400 ("chat not found") response marks the recipient inactive so the
        registry self-cleans.
        """
        if not self.is_configured:
            return False

        # Deduplicate chat IDs across subscribers registry and .env fallback
        unique_chat_ids: set[int] = set(self._registry.active_chat_ids())
        env_chat_id = os.getenv("TELEGRAM_OWNER_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
        if env_chat_id:
            try:
                unique_chat_ids.add(int(env_chat_id.strip()))
            except ValueError:
                pass
        chat_ids: list[int] = list(unique_chat_ids)
                    
        if not chat_ids:
            print(
                f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                "📭 [TELEGRAM] No active subscribers and no TELEGRAM_OWNER_CHAT_ID set; alert dropped."
            )
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        all_ok = True
        stamp = datetime.datetime.now().strftime("%H:%M:%S")

        for chat_id in list(chat_ids):
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            try:
                res = requests.post(url, json=payload, timeout=8)
                if res.status_code == 200:
                    print(f"[{stamp}] 📱 [TELEGRAM] Alert delivered to {chat_id}.")
                    continue

                # Try plain-text fallback before giving up.
                fallback_payload = {k: v for k, v in payload.items() if k != "parse_mode"}
                fallback_res = requests.post(url, json=fallback_payload, timeout=8)
                if fallback_res.status_code == 200:
                    print(f"[{stamp}] 📱 [TELEGRAM] Alert delivered to {chat_id} via plain-text fallback.")
                    continue

                self._handle_send_failure(chat_id, res.status_code, res.text)
                all_ok = False
            except Exception as e:
                print(f"[{stamp}] ⚠️ [TELEGRAM EXCEPTION] chat_id={chat_id} err={e}")
                all_ok = False

        return all_ok

    def _handle_send_failure(self, chat_id: int, status: int, body: str) -> None:
        """Reacts to a failed delivery: deactivates dead subscribers, logs everything else."""
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        if status in (400, 403):
            self._registry.mark_inactive(chat_id)
            print(
                f"[{stamp}] ⚠️ [TELEGRAM] chat_id={chat_id} returned {status} "
                f"(blocked/not-found). Marked inactive: {body[:160]}"
            )
        else:
            print(
                f"[{stamp}] ⚠️ [TELEGRAM ERROR] chat_id={chat_id} status={status} body={body[:160]}"
            )

    # --- typed helpers -----------------------------------------------------

    def send_system_error(
        self,
        component: str,
        error_msg: str,
        severity: str = "warning",
        action_taken: str = "",
        cooldown_seconds: Optional[int] = None,
    ) -> bool:
        """
        Dispatches a throttled operational/system error to all active subscribers.

        Args:
            component: short subsystem label (e.g. "OpenAlgo", "EngineLoop", "CircuitBreaker").
            error_msg: human-readable error description. Combined with ``component`` to form
                the throttle key, so two distinct errors from the same component each get
                their own debounce window.
            severity: one of ``"critical"`` (🚨), ``"warning"`` (⚠️), ``"halt"`` (🛑),
                ``"rejection"`` (❌). Unknown values fall back to the warning glyph.
            action_taken: optional recovery / mitigation note included in the message body.
            cooldown_seconds: override the default 15-minute cooldown (mainly for tests).

        Returns:
            True when the message was dispatched, False when throttled, when no token
            is configured, or when delivery failed.
        """
        component = (component or "Unknown").strip()
        error_msg = (error_msg or "Unspecified error").strip()
        severity_key = (severity or "warning").strip().lower()
        icon = _SEVERITY_ICON.get(severity_key, "⚠️")
        cooldown = (
            cooldown_seconds
            if cooldown_seconds is not None
            else self._error_cooldown_seconds
        )

        throttle_key = f"{component}::{error_msg}"
        now = datetime.datetime.now()
        with self._error_throttle_lock:
            last_sent = self._error_throttle.get(throttle_key)
            if last_sent is not None and (now - last_sent).total_seconds() < cooldown:
                # Suppressed by the debounce window. We still surface a log line so the
                # operator can see the alert was caught and intentionally not re-sent.
                print(
                    f"[{now.strftime('%H:%M:%S')}] 🔇 [TELEGRAM] Suppressed repeat "
                    f"system-error alert ({severity_key}/{component}) — "
                    f"cooldown {cooldown}s not elapsed."
                )
                return False
            self._error_throttle[throttle_key] = now

        action_block = (
            f"\n• *Action Taken:* {action_taken.strip()}"
            if action_taken and action_taken.strip()
            else ""
        )
        msg = (
            f"{icon} *[{severity_key.upper()} ALERT]* `{component}`\n"
            f"• *Time:* `{now.strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"• *Error:* {error_msg}"
            f"{action_block}"
        )
        return self.send_message(msg)

    def send_trade_entry(self, symbol: str, price: float, sl: float, tp: float, qty: int, direction: str = "SHORT", mode: str = "paper") -> bool:
        side_str = "LONG (Buy)" if str(direction).upper() == "LONG" else "SHORT (Sell)"
        msg = (
            f"🔔 *[{mode.upper()} ENTRY]* `{symbol}`\n"
            f"• *Side:* {side_str}\n"
            f"• *Price:* ₹{price:,.2f}\n"
            f"• *Quantity:* {qty}\n"
            f"• *Stop-Loss:* ₹{sl:,.2f}\n"
            f"• *Target:* ₹{tp:,.2f} (1:2 R:R)"
        )
        return self.send_message(msg)

    def send_trailing_sl(self, symbol: str, be_price: float, mode: str = "paper") -> bool:
        msg = (
            f"🛡️ *[{mode.upper()} TRAILING SL]* `{symbol}`\n"
            f"Reached +1R profit! Stop-Loss moved to Breakeven @ ₹{be_price:,.2f}."
        )
        return self.send_message(msg)

    def send_trade_exit(self, symbol: str, price: float, net_pnl: float, pnl_pct: float, reason: str, mode: str = "paper") -> bool:
        icon = "✅" if net_pnl > 0 else "❌"
        msg = (
            f"{icon} *[{mode.upper()} EXIT]* `{symbol}`\n"
            f"• *Exit Price:* ₹{price:,.2f}\n"
            f"• *Net PnL:* ₹{net_pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
            f"• *Result:* {reason}"
        )
        return self.send_message(msg)

    def send_eod_summary(self, report_text: str, mode: str = "paper") -> bool:
        # If report_text already has the summary header, avoid duplicate header
        if "Daily" in report_text and "Trading Summary" in report_text:
            msg = report_text
        else:
            msg = (
                f"📊 *[{mode.upper()} EOD REPORT]*\n\n"
                f"{report_text}"
            )
        return self.send_message(msg)
