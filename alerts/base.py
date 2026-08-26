"""
Abstract Base Alert Channel & Multi-Channel Alert Dispatcher.
"""

import abc
import os
from typing import Dict, Any, List, Optional
from config import CONFIG, TradingConfig


class BaseAlertChannel(abc.ABC):
    """Abstract interface that all notification channels must implement."""

    @abc.abstractmethod
    def send_message(self, text: str) -> bool:
        """Sends a raw formatted text message."""
        pass

    @abc.abstractmethod
    def send_trade_entry(self, symbol: str, price: float, sl: float, tp: float, qty: int, mode: str = "paper") -> bool:
        """Sends a trade entry notification."""
        pass

    @abc.abstractmethod
    def send_trailing_sl(self, symbol: str, be_price: float, mode: str = "paper") -> bool:
        """Sends a trailing Stop-Loss notification."""
        pass

    @abc.abstractmethod
    def send_trade_exit(self, symbol: str, price: float, net_pnl: float, pnl_pct: float, reason: str, mode: str = "paper") -> bool:
        """Sends a trade exit notification."""
        pass

    @abc.abstractmethod
    def send_eod_summary(self, report_text: str, mode: str = "paper") -> bool:
        """Sends an End-Of-Day performance summary."""
        pass

    @abc.abstractmethod
    def send_system_error(
        self,
        component: str,
        error_msg: str,
        severity: str = "warning",
        action_taken: str = "",
        cooldown_seconds: Optional[int] = None,
    ) -> bool:
        """Sends a throttled operational/system error notification."""
        pass


def get_active_channels(config: TradingConfig = CONFIG) -> List[BaseAlertChannel]:
    """
    Instantiates and returns the list of active alert channels based on config.ALERT_CHANNELS.
    Handles tuples, lists, or single strings gracefully:
      - None: config.ALERT_CHANNELS = () or []
      - One : config.ALERT_CHANNELS = ("telegram",) or ["telegram"] or "telegram"
      - Many: config.ALERT_CHANNELS = ("telegram", "discord", "email")
    """
    from alerts.telegram import TelegramAlertChannel

    channel_registry = {
        "telegram": TelegramAlertChannel,
    }

    raw_channels = config.ALERT_CHANNELS
    if isinstance(raw_channels, str):
        raw_channels = [raw_channels]

    channels = []
    for ch_name in raw_channels:
        ch_name_clean = str(ch_name).strip().lower()
        if ch_name_clean in channel_registry:
            channel_cls = channel_registry[ch_name_clean]
            channels.append(channel_cls())
    return channels


def notify_trade_entry(symbol: str, price: float, sl: float, tp: float, qty: int, mode: str = "paper", config: TradingConfig = CONFIG) -> None:
    """Broadcasts a trade entry notification across all active channels."""
    for ch in get_active_channels(config):
        ch.send_trade_entry(symbol=symbol, price=price, sl=sl, tp=tp, qty=qty, mode=mode)


def notify_trailing_sl(symbol: str, be_price: float, mode: str = "paper", config: TradingConfig = CONFIG) -> None:
    """Broadcasts a trailing Stop-Loss update across all active channels."""
    for ch in get_active_channels(config):
        ch.send_trailing_sl(symbol=symbol, be_price=be_price, mode=mode)


def notify_trade_exit(symbol: str, price: float, net_pnl: float, pnl_pct: float, reason: str, mode: str = "paper", config: TradingConfig = CONFIG) -> None:
    """Broadcasts a trade exit notification across all active channels."""
    for ch in get_active_channels(config):
        ch.send_trade_exit(symbol=symbol, price=price, net_pnl=net_pnl, pnl_pct=pnl_pct, reason=reason, mode=mode)


def notify_eod_summary(report_text: str, mode: str = "paper", config: TradingConfig = CONFIG) -> None:
    """Broadcasts an End-Of-Day performance report across all active channels."""
    for ch in get_active_channels(config):
        ch.send_eod_summary(report_text=report_text, mode=mode)


def notify_system_error(
    component: str,
    error_msg: str,
    severity: str = "warning",
    action_taken: str = "",
    cooldown_seconds: Optional[int] = None,
    config: TradingConfig = CONFIG,
) -> None:
    """
    Broadcasts a throttled operational/system error across all active channels.

    Channels are expected to debounce identical ``(component, error_msg)`` pairs so
    a sustained outage does not flood the operator's Telegram. The first occurrence
    is dispatched immediately; identical re-occurrences are throttled per-channel.
    """
    for ch in get_active_channels(config):
        ch.send_system_error(
            component=component,
            error_msg=error_msg,
            severity=severity,
            action_taken=action_taken,
            cooldown_seconds=cooldown_seconds,
        )
