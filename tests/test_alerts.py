"""
Unit tests for alerts/ notification channels and multi-channel dispatcher.

All tests redirect the subscribers DB to a per-test tmp path via the
TELEGRAM_SUBSCRIBERS_DB_PATH env var so they cannot pollute the real
database/telegram_subscribers.db.
"""

import os
import sys
import unittest
import tempfile
from unittest.mock import patch, MagicMock
from dataclasses import replace

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import CONFIG
from alerts.base import BaseAlertChannel
from alerts.telegram import TelegramAlertChannel
from alerts.subscribers import SubscribersRegistry, get_db_connection
from alerts import (
    get_active_channels,
    notify_trade_entry,
    notify_trailing_sl,
    notify_trade_exit,
    notify_eod_summary,
)


class TestAlertsPackage(unittest.TestCase):
    def setUp(self):
        self.original_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.original_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        self.original_invite = os.environ.get("TELEGRAM_INVITE_CODE")
        self.original_db_path = os.environ.get("TELEGRAM_SUBSCRIBERS_DB_PATH")

        # Isolate the registry DB per test.
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_db = os.path.join(self._tmpdir.name, "subscribers.db")
        os.environ["TELEGRAM_SUBSCRIBERS_DB_PATH"] = self.tmp_db

        # Clear stale env that could influence the channel / registry.
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)
        os.environ.pop("TELEGRAM_INVITE_CODE", None)

    def tearDown(self):
        if self.original_token is not None:
            os.environ["TELEGRAM_BOT_TOKEN"] = self.original_token
        else:
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        if self.original_chat_id is not None:
            os.environ["TELEGRAM_CHAT_ID"] = self.original_chat_id
        else:
            os.environ.pop("TELEGRAM_CHAT_ID", None)
        if self.original_invite is not None:
            os.environ["TELEGRAM_INVITE_CODE"] = self.original_invite
        else:
            os.environ.pop("TELEGRAM_INVITE_CODE", None)
        if self.original_db_path is not None:
            os.environ["TELEGRAM_SUBSCRIBERS_DB_PATH"] = self.original_db_path
        else:
            os.environ.pop("TELEGRAM_SUBSCRIBERS_DB_PATH", None)
        self._tmpdir.cleanup()

    # --- dispatcher wiring -------------------------------------------------

    def test_channel_selection_none(self):
        """Verifies that setting ALERT_CHANNELS = () yields 0 active channels."""
        cfg = replace(CONFIG, ALERT_CHANNELS=())
        channels = get_active_channels(config=cfg)
        self.assertEqual(len(channels), 0)

    def test_channel_selection_tuple(self):
        """Verifies that setting ALERT_CHANNELS = ('telegram',) activates Telegram channel."""
        cfg = replace(CONFIG, ALERT_CHANNELS=("telegram",))
        channels = get_active_channels(config=cfg)
        self.assertEqual(len(channels), 1)
        self.assertIsInstance(channels[0], TelegramAlertChannel)

    def test_channel_selection_string_graceful(self):
        """Verifies that accidental string input ('telegram') without comma is gracefully handled."""
        cfg = replace(CONFIG, ALERT_CHANNELS=("telegram"))  # Single string!
        channels = get_active_channels(config=cfg)
        self.assertEqual(len(channels), 1)
        self.assertIsInstance(channels[0], TelegramAlertChannel)

    # --- channel + registry integration ------------------------------------

    def test_telegram_unconfigured_fails_silently(self):
        """If TELEGRAM_BOT_TOKEN is not set, alerts fail silently."""
        channel = TelegramAlertChannel()
        self.assertFalse(channel.is_configured)
        self.assertFalse(channel.send_message("Test"))

    @patch("alerts.telegram.requests.post")
    def test_telegram_broadcasts_to_all_active_subscribers(self, mock_post):
        """A configured channel posts once per active subscriber in the registry."""
        os.environ["TELEGRAM_BOT_TOKEN"] = "bot12345"
        mock_post.return_value = MagicMock(status_code=200)

        # Pre-seed two active subscribers.
        registry = SubscribersRegistry()
        registry.subscribe(111, "alice", "Alice")
        registry.subscribe(222, "bob", "Bob")

        channel = TelegramAlertChannel()
        self.assertTrue(channel.is_configured)
        ok = channel.send_trade_entry(
            symbol="ONGC-EQ", price=286.50, sl=288.10, tp=283.30, qty=87, mode="paper"
        )

        self.assertTrue(ok)
        self.assertEqual(mock_post.call_count, 2)
        chat_ids = sorted(call.kwargs["json"]["chat_id"] for call in mock_post.call_args_list)
        self.assertEqual(chat_ids, [111, 222])
        first_text = mock_post.call_args_list[0].kwargs["json"]["text"]
        self.assertIn("[PAPER ENTRY]", first_text)
        self.assertIn("ONGC-EQ", first_text)

    @patch("alerts.telegram.requests.post")
    def test_broadcast_skips_inactive_subscribers(self, mock_post):
        """An active=0 subscriber must not receive the alert."""
        os.environ["TELEGRAM_BOT_TOKEN"] = "bot12345"
        mock_post.return_value = MagicMock(status_code=200)

        registry = SubscribersRegistry()
        registry.subscribe(111, "alice", "Alice")
        registry.subscribe(222, "bob", "Bob")
        registry.unsubscribe(222)

        channel = TelegramAlertChannel()
        self.assertTrue(channel.send_trade_entry(
            symbol="INFY-EQ", price=1450.0, sl=1460.0, tp=1430.0, qty=10, mode="paper"
        ))
        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(mock_post.call_args.kwargs["json"]["chat_id"], 111)

    @patch("alerts.telegram.requests.post")
    def test_no_subscribers_drops_alert(self, mock_post):
        """With an empty registry, send_message returns False and does not POST."""
        os.environ["TELEGRAM_BOT_TOKEN"] = "bot12345"
        channel = TelegramAlertChannel()
        self.assertFalse(channel.send_message("hello"))
        mock_post.assert_not_called()

    @patch("alerts.telegram.requests.post")
    def test_mark_inactive_called_on_403(self, mock_post):
        """When Telegram returns 403 for a recipient, the channel marks them inactive."""
        os.environ["TELEGRAM_BOT_TOKEN"] = "bot12345"
        mock_resp = MagicMock(status_code=403, text="bot was blocked by the user")
        mock_post.return_value = mock_resp

        registry = SubscribersRegistry()
        registry.subscribe(999, "ghost", "Ghost")

        channel = TelegramAlertChannel()
        ok = channel.send_trade_entry(
            symbol="TCS-EQ", price=3500.0, sl=3510.0, tp=3480.0, qty=5, mode="live"
        )
        self.assertFalse(ok)
        # The recipient should now be marked inactive.
        self.assertNotIn(999, registry.active_chat_ids())

    # --- registry lifecycle -------------------------------------------------

    def test_subscribers_registry_subscribe_unsubscribe_ban(self):
        registry = SubscribersRegistry()
        registry.subscribe(101, "u1", "User One")
        registry.subscribe(102, "u2", "User Two")
        self.assertEqual(sorted(registry.active_chat_ids()), [101, 102])

        # Banned users must be excluded from the broadcast list.
        registry.mark_banned(102)
        self.assertEqual(registry.active_chat_ids(), [101])
        self.assertTrue(registry.is_banned(102))

        # Reinstate clears the ban and re-activates.
        self.assertTrue(registry.reinstate(102))
        self.assertEqual(sorted(registry.active_chat_ids()), [101, 102])
        self.assertFalse(registry.is_banned(102))

        # Unsubscribe flips active=0 (still recoverable).
        self.assertTrue(registry.unsubscribe(101))
        self.assertEqual(registry.active_chat_ids(), [102])

    def test_seed_env_chat_id_inserts_only_once(self):
        os.environ["TELEGRAM_CHAT_ID"] = "555000"
        registry = SubscribersRegistry()
        self.assertTrue(registry.seed_env_chat_id())
        self.assertIn(555000, registry.active_chat_ids())
        # Second call should be a no-op.
        self.assertFalse(registry.seed_env_chat_id())
        with get_db_connection() as conn:
            rows = conn.execute("SELECT chat_id FROM subscribers").fetchall()
        self.assertEqual(len(rows), 1)

    def test_seed_env_chat_id_invalid_is_noop(self):
        os.environ["TELEGRAM_CHAT_ID"] = "not-a-number"
        registry = SubscribersRegistry()
        self.assertFalse(registry.seed_env_chat_id())
        self.assertEqual(registry.active_chat_ids(), [])

    def test_invite_code_verification_ban_after_two_wrong_tries(self):
        os.environ["TELEGRAM_INVITE_CODE"] = "secret-123"
        registry = SubscribersRegistry()
        registry.start_invite(701, "x", "X")

        self.assertFalse(registry.verify_invite(701, "wrong-1"))
        self.assertFalse(registry.is_banned(701))

        self.assertFalse(registry.verify_invite(701, "wrong-2"))
        self.assertTrue(registry.is_banned(701))

    def test_invite_code_correct_code_activates(self):
        os.environ["TELEGRAM_INVITE_CODE"] = "secret-123"
        registry = SubscribersRegistry()
        registry.start_invite(702, "y", "Y")

        self.assertTrue(registry.verify_invite(702, "secret-123"))
        self.assertIn(702, registry.active_chat_ids())

    def test_invite_code_empty_env_rejects_all(self):
        os.environ["TELEGRAM_INVITE_CODE"] = ""
        registry = SubscribersRegistry()
        registry.start_invite(703, "z", "Z")
        self.assertFalse(registry.verify_invite(703, "anything"))
        self.assertNotIn(703, registry.active_chat_ids())

    # --- dispatcher contract -----------------------------------------------

    @patch.object(TelegramAlertChannel, "send_trailing_sl")
    def test_notify_trailing_sl_broadcast(self, mock_trail):
        """Verifies dispatcher broadcasts trailing SL alerts."""
        notify_trailing_sl(symbol="RELIANCE-EQ", be_price=1315.00, mode="paper")
        mock_trail.assert_called_once_with(symbol="RELIANCE-EQ", be_price=1315.00, mode="paper")

    @patch.object(TelegramAlertChannel, "send_trade_exit")
    def test_notify_trade_exit_broadcast(self, mock_exit):
        """Verifies dispatcher broadcasts trade exit alerts."""
        notify_trade_exit(
            symbol="INFY-EQ", price=1450.00, net_pnl=232.50, pnl_pct=1.85,
            reason="TARGET HIT ✅", mode="paper",
        )
        mock_exit.assert_called_once_with(
            symbol="INFY-EQ", price=1450.00, net_pnl=232.50, pnl_pct=1.85,
            reason="TARGET HIT ✅", mode="paper",
        )


if __name__ == "__main__":
    unittest.main()
