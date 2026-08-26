"""
Centralized Alerts & Notifications Package.

Exports clean public API for trade notifications across configured mediums.
"""

from alerts.base import (
    BaseAlertChannel,
    get_active_channels,
    notify_trade_entry,
    notify_trailing_sl,
    notify_trade_exit,
    notify_eod_summary,
)
from alerts.telegram import TelegramAlertChannel
from alerts.subscribers import SubscribersRegistry

__all__ = [
    "BaseAlertChannel",
    "TelegramAlertChannel",
    "SubscribersRegistry",
    "get_active_channels",
    "notify_trade_entry",
    "notify_trailing_sl",
    "notify_trade_exit",
    "notify_eod_summary",
]
