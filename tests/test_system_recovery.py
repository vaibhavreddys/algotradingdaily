"""
Unit tests for the Automated Infrastructure Self-Healing & Diagnostic Subsystem
(core/system_recovery.py, alerts/tg_bot.py, and BaseTradingEngine integration).
"""

import os
import time
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from config import TradingConfig
from core.system_recovery import (
    probe_openalgo_gateway,
    probe_broker_auth,
    probe_system_resources,
    diagnose_system,
    recover_broker_session,
    auto_heal_all,
)
from alerts.tg_bot import BOT_COMMAND_LIST
from live_trading.base_engine import BaseTradingEngine


class TestSystemRecovery(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(
            OPENALGO_HOST="http://127.0.0.1:5000",
            OPENALGO_API_KEY="test_algo_key",
            TRADING_MODE="paper",
        )

    @patch("requests.post")
    def test_probe_openalgo_gateway_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        probe = probe_openalgo_gateway(self.config)
        self.assertTrue(probe["healthy"])
        self.assertEqual(probe["code"], 200)

    @patch("requests.post")
    def test_probe_openalgo_gateway_error(self, mock_post):
        import requests
        mock_post.side_effect = requests.RequestException("Connection refused")

        probe = probe_openalgo_gateway(self.config)
        self.assertFalse(probe["healthy"])
        self.assertEqual(probe["code"], 0)
        self.assertIn("Connection refused", probe["detail"])

    @patch("requests.post")
    def test_probe_broker_auth_authenticated(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "data": {"available_margin": 15000.50, "cash": 15000.50},
        }
        mock_post.return_value = mock_resp

        probe = probe_broker_auth(self.config)
        self.assertTrue(probe["authenticated"])
        self.assertEqual(probe["avail_margin"], 15000.50)
        self.assertEqual(probe["cash"], 15000.50)

    @patch("requests.post")
    def test_probe_broker_auth_session_expired(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "error",
            "message": "Session expired (login required)",
        }
        mock_post.return_value = mock_resp

        probe = probe_broker_auth(self.config)
        self.assertFalse(probe["authenticated"])
        self.assertIn("Session expired", probe["detail"])

    def test_probe_system_resources(self):
        resources = probe_system_resources()
        self.assertIn("disk_pct", resources)
        self.assertIn("disk_free_gb", resources)
        self.assertIsInstance(resources["healthy"], bool)

    @patch("core.system_recovery.probe_openalgo_gateway")
    @patch("core.system_recovery.probe_broker_auth")
    def test_diagnose_system_aggregates_health(self, mock_broker, mock_gw):
        mock_gw.return_value = {"healthy": True, "code": 200, "host": "http://127.0.0.1:5000", "detail": "Responding"}
        mock_broker.return_value = {"authenticated": True, "detail": "Authenticated", "avail_margin": 25000.0, "cash": 25000.0}

        diag = diagnose_system(self.config)
        self.assertTrue(diag["gateway"]["healthy"])
        self.assertTrue(diag["broker"]["authenticated"])
        self.assertTrue(diag["all_healthy"])

    @patch("core.system_recovery.get_auto_login_script_and_python")
    def test_recover_broker_session_cooldown_throttling(self, mock_get_paths):
        # First call establishes recent timestamp
        import core.system_recovery as sr
        sr._LAST_BROKER_RECOVERY_TIMESTAMP = time.time() - 10  # 10s ago, cooldown is 300s

        ok, msg = recover_broker_session(self.config, force=False)
        self.assertFalse(ok)
        self.assertIn("Cooldown active", msg)

    @patch("subprocess.run")
    @patch("core.system_recovery.probe_broker_auth")
    @patch("core.system_recovery.get_auto_login_script_and_python")
    def test_recover_broker_session_force_bypasses_cooldown(self, mock_get_paths, mock_probe, mock_sub):
        mock_get_paths.return_value = (Path("/fake/auto_login_shoonya.py"), "/fake/python")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_sub.return_value = mock_proc
        mock_probe.return_value = {"authenticated": True, "avail_margin": 5000.0}

        import core.system_recovery as sr
        sr._LAST_BROKER_RECOVERY_TIMESTAMP = time.time() - 10  # within cooldown window

        ok, msg = recover_broker_session(self.config, force=True)
        self.assertTrue(ok)
        self.assertIn("Shoonya re-authenticated successfully", msg)
        self.assertIn("₹5,000.00", msg)

    @patch("core.system_recovery.recover_gateway")
    @patch("core.system_recovery.recover_broker_session")
    @patch("core.system_recovery.diagnose_system")
    def test_auto_heal_all_triggers_targeted_recovery(self, mock_diag, mock_rec_broker, mock_rec_gw):
        # Initial: Gateway down, Broker unauthenticated
        mock_diag.side_effect = [
            {
                "gateway": {"healthy": False, "detail": "Connection refused"},
                "broker": {"authenticated": False, "detail": "Session expired"},
                "resources": {"healthy": True},
                "all_healthy": False,
            },
            # Final: All healthy
            {
                "gateway": {"healthy": True, "detail": "Responding"},
                "broker": {"authenticated": True, "detail": "Authenticated", "avail_margin": 10000.0},
                "resources": {"healthy": True},
                "all_healthy": True,
            },
        ]
        mock_rec_gw.return_value = (True, "Gateway restarted")
        mock_rec_broker.return_value = (True, "Shoonya re-authenticated")

        report = auto_heal_all(self.config)
        self.assertTrue(report["all_healed"])
        mock_rec_gw.assert_called_once()
        mock_rec_broker.assert_called_once()
        self.assertEqual(len(report["actions"]), 2)

    def test_dynamic_strategy_timeframe_parsing(self):
        engine = BaseTradingEngine()

        engine.timeframe = "5m"
        self.assertEqual(engine.get_strategy_interval_seconds(), 300)

        engine.timeframe = "15m"
        self.assertEqual(engine.get_strategy_interval_seconds(), 900)

        engine.timeframe = "1h"
        self.assertEqual(engine.get_strategy_interval_seconds(), 3600)

    def test_tg_bot_command_list_includes_recovery_commands(self):
        cmd_names = [cmd.command for cmd in BOT_COMMAND_LIST]
        self.assertIn("recover", cmd_names)
        self.assertIn("relogin", cmd_names)
        self.assertIn("health", cmd_names)

    def test_system_watchdog_cli_weekend_noop(self):
        import scripts.system_watchdog as watchdog
        with patch("datetime.datetime") as mock_dt:
            fake_now = MagicMock()
            fake_now.weekday.return_value = 6  # Sunday
            fake_now.strftime.return_value = "Sunday"
            mock_dt.now.return_value = fake_now
            self.assertEqual(watchdog.main(), 0)


if __name__ == "__main__":
    unittest.main()
