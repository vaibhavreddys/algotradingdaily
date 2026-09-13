"""
Unit tests for the Universal Singleton Process Lock (core/process_lock.py).
"""

import os
import shutil
import tempfile
import unittest
from core.process_lock import SingletonLock, AnotherInstanceRunningError


class TestProcessLock(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="lock_test_")

    def tearDown(self):
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass

    def test_single_acquisition_and_release(self):
        lock = SingletonLock(service_name="engine_live", lock_dir=self.test_dir)
        self.assertFalse(lock.is_acquired)

        acquired = lock.acquire()
        self.assertTrue(acquired)
        self.assertTrue(lock.is_acquired)
        self.assertTrue(lock.lock_file.exists())
        self.assertEqual(lock.get_locked_pid(), os.getpid())

        lock.release()
        self.assertFalse(lock.is_acquired)

    def test_conflict_raises_error(self):
        lock1 = SingletonLock(service_name="telegram_bot", lock_dir=self.test_dir)
        self.assertTrue(lock1.acquire())

        lock2 = SingletonLock(service_name="telegram_bot", lock_dir=self.test_dir, raise_on_conflict=True)
        with self.assertRaises(AnotherInstanceRunningError) as ctx:
            lock2.acquire()

        self.assertEqual(ctx.exception.service_name, "telegram_bot")
        self.assertEqual(ctx.exception.pid, os.getpid())

        lock1.release()

    def test_conflict_returns_false_when_not_raising(self):
        lock1 = SingletonLock(service_name="shoonya_login", lock_dir=self.test_dir)
        self.assertTrue(lock1.acquire())

        lock2 = SingletonLock(service_name="shoonya_login", lock_dir=self.test_dir, raise_on_conflict=False)
        self.assertFalse(lock2.acquire())
        self.assertFalse(lock2.is_acquired)

        lock1.release()

    def test_context_manager_auto_release(self):
        with SingletonLock(service_name="system_watchdog", lock_dir=self.test_dir) as lock:
            self.assertTrue(lock.is_acquired)

        # After exiting context manager, lock should be immediately re-acquirable
        lock2 = SingletonLock(service_name="system_watchdog", lock_dir=self.test_dir)
        self.assertTrue(lock2.acquire())
        lock2.release()

    def test_context_manager_releases_on_exception(self):
        try:
            with SingletonLock(service_name="failing_service", lock_dir=self.test_dir):
                raise ValueError("Simulated unexpected crash inside service")
        except ValueError:
            pass

        # Lock must be released despite exception
        lock2 = SingletonLock(service_name="failing_service", lock_dir=self.test_dir)
        self.assertTrue(lock2.acquire())
        lock2.release()

    def test_independent_services_do_not_conflict(self):
        engine_lock = SingletonLock(service_name="engine_live", lock_dir=self.test_dir)
        bot_lock = SingletonLock(service_name="telegram_bot", lock_dir=self.test_dir)
        watchdog_lock = SingletonLock(service_name="system_watchdog", lock_dir=self.test_dir)

        self.assertTrue(engine_lock.acquire())
        self.assertTrue(bot_lock.acquire())
        self.assertTrue(watchdog_lock.acquire())

        self.assertTrue(engine_lock.is_acquired)
        self.assertTrue(bot_lock.is_acquired)
        self.assertTrue(watchdog_lock.is_acquired)

        engine_lock.release()
        bot_lock.release()
        watchdog_lock.release()

    def test_reacquisition_after_release(self):
        lock = SingletonLock(service_name="reentrant_check", lock_dir=self.test_dir)
        self.assertTrue(lock.acquire())
        lock.release()

        # Re-acquire with same instance
        self.assertTrue(lock.acquire())
        lock.release()

    def test_base_engine_singleton_lock_aborts_duplicate(self):
        from unittest.mock import patch, MagicMock
        from live_trading.base_engine import BaseTradingEngine

        engine = BaseTradingEngine(mode="paper")
        # Hold the lock prior to engine starting
        external_lock = SingletonLock(service_name="engine_paper")
        self.assertTrue(external_lock.acquire())

        try:
            with patch("live_trading.base_engine.notify_system_error") as mock_notify:
                # Attempting to run the engine while lock is held externally
                engine.run_live_loop()
                mock_notify.assert_called_once()
                self.assertEqual(mock_notify.call_args[1]["component"], "EngineStartup")
                self.assertIn("already active", mock_notify.call_args[1]["error_msg"])
        finally:
            external_lock.release()

    def test_tg_bot_main_singleton_lock_aborts_duplicate(self):
        from unittest.mock import patch
        import alerts.tg_bot as tg_bot

        external_lock = SingletonLock(service_name="telegram_bot")
        self.assertTrue(external_lock.acquire())

        try:
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "mock:token", "TELEGRAM_INVITE_CODE": "mock_code"}):
                exit_code = tg_bot.main()
                self.assertEqual(exit_code, 1)
        finally:
            external_lock.release()

    def test_mcx_downloader_singleton_lock_aborts_duplicate(self):
        from unittest.mock import MagicMock
        from data_pipeline.shoonya_mcx.downloader import MCXIngestionEngine

        external_lock = SingletonLock(service_name="duckdb_ingest")
        self.assertTrue(external_lock.acquire())

        try:
            engine = MCXIngestionEngine.__new__(MCXIngestionEngine)
            # Calling engine.run() while lock is held must abort and return 0 immediately
            result = engine.run(commodities=["GOLD"])
            self.assertEqual(result, 0)
        finally:
            external_lock.release()


if __name__ == "__main__":
    unittest.main()
