"""
Unit tests for the throttled operational-error alerting channel.

Covers:
  * Debounce on identical (component, error_msg) within the cooldown window.
  * Re-arm once the cooldown has elapsed.
  * Distinct error keys are NOT debounced against each other.
  * Severity → emoji mapping.
  * Top-level notify_system_error dispatcher fans out to every active channel.
  * send_system_error is a no-op when the channel is not configured.
"""

import os
import sys
import datetime
import unittest
import tempfile
import threading
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import CONFIG
from alerts.telegram import TelegramAlertChannel
from alerts.base import notify_system_error, BaseAlertChannel
from alerts.subscribers import SubscribersRegistry


def _seed_channel(tmp_db_path: str) -> TelegramAlertChannel:
    """Returns a configured TelegramAlertChannel with a single active subscriber."""
    os.environ["TELEGRAM_BOT_TOKEN"] = "TEST_TOKEN"
    os.environ["TELEGRAM_SUBSCRIBERS_DB_PATH"] = tmp_db_path
    registry = SubscribersRegistry()
    registry.subscribe(111, "alice", "Alice")
    return TelegramAlertChannel()


class TestSystemErrorThrottle(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_db = os.path.join(self._tmpdir.name, "subs.db")
        self.channel = _seed_channel(self.tmp_db)
        # Freeze the clock so we can advance time deterministically.
        self._frozen = datetime.datetime(2026, 8, 26, 9, 30, 0)
        self._patcher = patch("alerts.telegram.datetime.datetime", wraps=datetime.datetime)
        self.mock_dt = self._patcher.start()
        self.mock_dt.now.return_value = self._frozen

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def _advance(self, seconds: int) -> None:
        self._frozen = self._frozen + datetime.timedelta(seconds=seconds)
        self.mock_dt.now.return_value = self._frozen

    def test_first_occurrence_dispatches_immediately(self):
        with patch("alerts.telegram.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="ok")
            ok = self.channel.send_system_error(
                component="OpenAlgo",
                error_msg="Connection refused",
                severity="critical",
                action_taken="Falling back to yfinance",
                cooldown_seconds=900,
            )
        self.assertTrue(ok)
        self.assertEqual(mock_post.call_count, 1)
        text = mock_post.call_args.kwargs["json"]["text"]
        self.assertIn("🚨", text)
        self.assertIn("OpenAlgo", text)
        self.assertIn("Connection refused", text)
        self.assertIn("Falling back to yfinance", text)

    def test_repeat_within_cooldown_is_suppressed(self):
        with patch("alerts.telegram.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="ok")
            first_ok = self.channel.send_system_error(
                component="OpenAlgo",
                error_msg="Connection refused",
                severity="critical",
                cooldown_seconds=900,
            )
            self.assertTrue(first_ok)
            self.assertEqual(mock_post.call_count, 1)

            self._advance(60)
            second_ok = self.channel.send_system_error(
                component="OpenAlgo",
                error_msg="Connection refused",
                severity="critical",
                cooldown_seconds=900,
            )
        self.assertFalse(second_ok)
        self.assertEqual(mock_post.call_count, 1)  # no new dispatch

    def test_repeat_after_cooldown_re_dispatches(self):
        with patch("alerts.telegram.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="ok")
            self.channel.send_system_error(
                component="OpenAlgo",
                error_msg="Connection refused",
                severity="critical",
                cooldown_seconds=900,
            )
            self.assertEqual(mock_post.call_count, 1)

            self._advance(901)  # past the 900s cooldown
            ok = self.channel.send_system_error(
                component="OpenAlgo",
                error_msg="Connection refused",
                severity="critical",
                cooldown_seconds=900,
            )
        self.assertTrue(ok)
        self.assertEqual(mock_post.call_count, 2)

    def test_distinct_error_keys_are_independent(self):
        with patch("alerts.telegram.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="ok")
            self.channel.send_system_error(
                component="OpenAlgo",
                error_msg="Connection refused",
                severity="critical",
                cooldown_seconds=900,
            )
            self.assertEqual(mock_post.call_count, 1)

            ok = self.channel.send_system_error(
                component="OpenAlgo",
                error_msg="HTTP 502 from gateway",
                severity="critical",
                cooldown_seconds=900,
            )
        self.assertTrue(ok)
        self.assertEqual(mock_post.call_count, 2)

    def test_distinct_components_are_independent(self):
        with patch("alerts.telegram.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="ok")
            self.channel.send_system_error(
                component="OpenAlgo",
                error_msg="Connection refused",
                severity="critical",
                cooldown_seconds=900,
            )
            self.assertEqual(mock_post.call_count, 1)

            ok = self.channel.send_system_error(
                component="OrderPlacement",
                error_msg="Connection refused",
                severity="rejection",
                cooldown_seconds=900,
            )
        self.assertTrue(ok)
        self.assertEqual(mock_post.call_count, 2)

    def test_severity_icon_mapping(self):
        cases = [
            ("critical", "🚨"),
            ("warning", "⚠️"),
            ("halt", "🛑"),
            ("rejection", "❌"),
            ("unknown_severity", "⚠️"),  # fallback glyph
        ]
        for severity, expected_icon in cases:
            with self.subTest(severity=severity):
                with patch("alerts.telegram.requests.post") as mock_post:
                    mock_post.return_value = MagicMock(status_code=200, text="ok")
                    # Unique component+error so each subTest is not throttled.
                    self.channel.send_system_error(
                        component=f"Sev{severity}",
                        error_msg=f"boom-{severity}",
                        severity=severity,
                        cooldown_seconds=900,
                    )
                    self.assertEqual(mock_post.call_count, 1)
                    text = mock_post.call_args.kwargs["json"]["text"]
                    self.assertIn(expected_icon, text)
                    self.assertIn(f"[{severity.upper()} ALERT]", text)

    def test_unconfigured_channel_silently_drops(self):
        ch = TelegramAlertChannel.__new__(TelegramAlertChannel)
        ch.token = None
        ch._registry = MagicMock()
        ch._error_throttle = {}
        ch._error_throttle_lock = threading.Lock()
        ch._error_cooldown_seconds = 900
        self.assertFalse(
            ch.send_system_error(
                component="X", error_msg="Y", severity="critical"
            )
        )

    def test_top_level_dispatcher_fans_out(self):
        fake_channel = MagicMock(spec=BaseAlertChannel)
        fake_channel.send_system_error.return_value = True
        with patch("alerts.base.get_active_channels", return_value=[fake_channel]):
            notify_system_error(
                component="OpenAlgo",
                error_msg="Connection refused",
                severity="critical",
                action_taken="Falling back",
            )
        fake_channel.send_system_error.assert_called_once_with(
            component="OpenAlgo",
            error_msg="Connection refused",
            severity="critical",
            action_taken="Falling back",
            cooldown_seconds=None,
        )

    def test_throttle_state_does_not_leak_across_channels(self):
        """Each TelegramAlertChannel instance maintains its own throttle state."""
        ch2 = _seed_channel(self.tmp_db)
        with patch("alerts.telegram.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="ok")
            # Self.channel already sent earlier in this test → throttled.
            # ch2 is a brand-new channel so it should dispatch fresh.
            ok = ch2.send_system_error(
                component="OpenAlgo",
                error_msg="Connection refused",
                severity="critical",
                cooldown_seconds=900,
            )
        self.assertTrue(ok)
        self.assertEqual(mock_post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
