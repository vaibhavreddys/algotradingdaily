"""
Telegram Notification Channel Implementation.

Broadcasts every alert to the set of active subscribers stored in
`database/telegram_subscribers.db` (see alerts.subscribers). The
single-recipient `chat_id` constructor argument is preserved as a
deprecated no-op so existing call sites keep importing.
"""

import os
import requests
import datetime
import warnings
from typing import Optional, Iterable

from alerts.base import BaseAlertChannel
from alerts.subscribers import SubscribersRegistry


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

        chat_ids: Iterable[int] = self._registry.active_chat_ids()
        if not chat_ids:
            print(
                f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                "📭 [TELEGRAM] No active subscribers; alert dropped."
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

    def send_trade_entry(self, symbol: str, price: float, sl: float, tp: float, qty: int, mode: str = "paper") -> bool:
        msg = (
            f"🔔 *[{mode.upper()} ENTRY]* `{symbol}`\n"
            f"• *Side:* SHORT (Sell)\n"
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
        msg = (
            f"📊 *[{mode.upper()} EOD REPORT]*\n\n"
            f"{report_text}"
        )
        return self.send_message(msg)
