"""
Unit tests for the on-demand Telegram bot commands (/pnl, /positions,
/status, /summary) added for issue #32.

The test isolates the subscribers DB to a tmp path so the live
database/telegram_subscribers.db is never touched. The trading DB read
helpers (get_active_positions / get_trade_journal / get_today_realized_pnl)
are patched in the bot module so the tests run hermetically.
"""

import os
import sys
import asyncio
import datetime
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Ensure required env before importing the bot module.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
os.environ.setdefault("TELEGRAM_INVITE_CODE", "TEST_INVITE")


def _run(coro):
    """Helper: drive a coroutine to completion in a fresh loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_update(chat_id: int, *, owner: bool = False) -> MagicMock:
    """Builds a minimal telegram.Update mock with a chat of the given id."""
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.username = "alice"
    update.effective_user.first_name = "Alice"
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


class TestOnDemandCommands(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["TELEGRAM_SUBSCRIBERS_DB_PATH"] = os.path.join(self._tmpdir.name, "subs.db")
        # Make sure we re-import the registry with the new env var if needed.
        from alerts.subscribers import SubscribersRegistry
        self.registry = SubscribersRegistry()
        self.registry.subscribe(111, "alice", "Alice")
        self.registry.subscribe(222, "bob", "Bob")
        # Unset any prior owner env so tests are deterministic unless overridden.
        os.environ.pop("TELEGRAM_OWNER_CHAT_ID", None)

        # Import the bot module lazily so env is in place first.
        from alerts import tg_bot
        self.tg_bot = tg_bot

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("TELEGRAM_SUBSCRIBERS_DB_PATH", None)

    # --- /pnl builder ----------------------------------------------------

    def test_pnl_text_empty_journal(self):
        with patch.object(self.tg_bot, "get_today_realized_pnl", return_value=0.0), \
             patch.object(self.tg_bot, "get_trade_journal", return_value=[]), \
             patch.object(self.tg_bot, "get_active_positions", return_value=[]):
            text = self.tg_bot._build_pnl_text()
        self.assertIn("TODAY P&L SCORECARD", text)
        self.assertIn("Total Trades: 0", text)
        self.assertIn("0 Wins, 0 Losses", text)
        self.assertIn("+₹0.00", text)

    def test_pnl_text_with_trades(self):
        today_prefix = "2026-08-27"
        journal = [
            {"id": 1, "net_pnl": 500.0, "balance_after_trade": 100500.0, "exit_time": f"{today_prefix} 09:30:00"},
            {"id": 2, "net_pnl": 950.0, "balance_after_trade": 101450.0, "exit_time": f"{today_prefix} 11:00:00"},
            {"id": 3, "net_pnl": -200.0, "balance_after_trade": 101250.0, "exit_time": f"{today_prefix} 14:00:00"},
        ]
        with patch.object(self.tg_bot, "get_today_realized_pnl", return_value=1250.0), \
             patch.object(self.tg_bot, "get_trade_journal", return_value=journal), \
             patch.object(self.tg_bot, "get_active_positions", return_value=[]):
            with patch("alerts.tg_bot.datetime.datetime") as mock_dt:
                mock_dt.now.return_value.strftime.return_value = today_prefix
                text = self.tg_bot._build_pnl_text()
        self.assertIn("Realized P&L: +₹1,250.00", text)
        self.assertIn("Total Trades: 3 (2 Wins, 1 Losses)", text)
        self.assertIn("Ending Capital: ₹101,250.00", text)

    def test_pnl_text_includes_open_mtm_when_tick_available(self):
        with patch.object(self.tg_bot, "get_today_realized_pnl", return_value=0.0), \
             patch.object(self.tg_bot, "get_trade_journal", return_value=[]), \
             patch.object(self.tg_bot, "get_active_positions", return_value=[
                 {"symbol": "RELIANCE-EQ", "entry_price": 1300.0, "quantity": 100},
             ]), \
             patch.object(self.tg_bot, "_safe_ltp", return_value={"ltp": 1290.0}):
            text = self.tg_bot._build_pnl_text()
        # Short-side: ltp < entry → profit. (1300 - 1290) * 100 = ₹1000.
        self.assertIn("Open MTM P&L: +₹1,000.00", text)
        self.assertIn("across 1 open", text)

    def test_pnl_text_handles_tick_unavailable(self):
        with patch.object(self.tg_bot, "get_today_realized_pnl", return_value=0.0), \
             patch.object(self.tg_bot, "get_trade_journal", return_value=[]), \
             patch.object(self.tg_bot, "get_active_positions", return_value=[
                 {"symbol": "TCS-EQ", "entry_price": 4000.0, "quantity": 10},
             ]), \
             patch.object(self.tg_bot, "_safe_ltp", return_value=None):
            text = self.tg_bot._build_pnl_text()
        self.assertIn("n/a", text)

    # --- /positions builder ---------------------------------------------

    def test_positions_text_no_positions(self):
        with patch.object(self.tg_bot, "get_active_positions", return_value=[]):
            text = self.tg_bot._build_positions_text()
        self.assertEqual(text, "💤 No open positions currently active.")

    def test_positions_text_renders_trailed_breakeven(self):
        with patch.object(self.tg_bot, "get_active_positions", return_value=[
            {
                "symbol": "RELIANCE-EQ",
                "quantity": 100,
                "entry_price": 1302.5,
                "current_sl": 1302.5,  # sl == entry → trailed
                "target_price": 1277.5,
            }
        ]), patch.object(self.tg_bot, "_safe_ltp", return_value={"ltp": 1300.0}):
            text = self.tg_bot._build_positions_text()
        self.assertIn("ACTIVE OPEN POSITIONS", text)
        self.assertIn("RELIANCE", text)
        self.assertIn("🛡️ Trailed Breakeven", text)
        self.assertIn("Target: ₹1,277.50", text)
        # MTM: short, 100 qty, (1302.5 - 1300) = +250
        self.assertIn("+₹250.00", text)

    def test_positions_text_no_trailing_when_sl_above_entry(self):
        with patch.object(self.tg_bot, "get_active_positions", return_value=[
            {
                "symbol": "TCS-EQ",
                "quantity": 10,
                "entry_price": 4000.0,
                "current_sl": 4010.0,  # sl > entry → NOT trailed
                "target_price": 3980.0,
            }
        ]), patch.object(self.tg_bot, "_safe_ltp", return_value=None):
            text = self.tg_bot._build_positions_text()
        self.assertIn("₹4,010.00", text)
        self.assertNotIn("Trailed Breakeven", text)
        self.assertIn("n/a", text)

    # --- /status builder -------------------------------------------------

    def test_status_text_uses_config_mode_and_universe(self):
        # CONFIG is a frozen dataclass; instead of mutating it, exercise the
        # builder with the live config and assert the labels match what it
        # currently advertises.
        from config import CONFIG as live_config
        with patch.object(self.tg_bot, "_probe_engine_heartbeat", return_value=("🟢", "Active (last write 1.0m ago)")), \
             patch.object(self.tg_bot, "_probe_market_status", return_value=("🟢", "Open (NSE)")), \
             patch.object(self.tg_bot, "_next_scan_time", return_value="10:15:00 IST"):
            text = self.tg_bot._build_status_text()
        self.assertIn("SYSTEM STATUS", text)
        self.assertIn(f"{live_config.TRADING_MODE.upper()} TRADING", text)
        self.assertIn("🟢 Active", text)
        self.assertIn("🟢 Open (NSE)", text)
        self.assertIn(live_config.UNIVERSE.upper(), text)
        self.assertIn("Next 15m Scan: 10:15:00 IST", text)

    def test_status_text_engine_stalled(self):
        with patch.object(self.tg_bot, "_probe_engine_heartbeat", return_value=("🟠", "Stalled (last write 60m ago)")), \
             patch.object(self.tg_bot, "_probe_market_status", return_value=("🟢", "Open (NSE)")), \
             patch.object(self.tg_bot, "_next_scan_time", return_value="10:15:00 IST"):
            text = self.tg_bot._build_status_text()
        self.assertIn("🟠 Stalled", text)

    def test_status_text_market_closed(self):
        with patch.object(self.tg_bot, "_probe_engine_heartbeat", return_value=("🟢", "Active (last write 5m ago)")), \
             patch.object(self.tg_bot, "_probe_market_status", return_value=("🔴", "Closed — reopens at 09:15:00 IST")), \
             patch.object(self.tg_bot, "_next_scan_time", return_value="09:15:00 IST (market open)"):
            text = self.tg_bot._build_status_text()
        self.assertIn("🔴 Closed", text)
        self.assertIn("reopens at 09:15:00 IST", text)

    def test_probe_engine_heartbeat_no_trades_yet(self):
        with patch("core.trade_db.get_trade_journal", return_value=[]):
            icon, text = self.tg_bot._probe_engine_heartbeat("paper")
        self.assertEqual(icon, "🟡")
        self.assertIn("No trades yet", text)

    def test_probe_engine_heartbeat_fresh_write(self):
        now = datetime.datetime.now()
        with patch("core.trade_db.get_trade_journal", return_value=[
            {"exit_time": now.strftime("%Y-%m-%d %H:%M:%S")}
        ]):
            icon, text = self.tg_bot._probe_engine_heartbeat("paper", stale_minutes=30)
        self.assertEqual(icon, "🟢")
        self.assertIn("Active", text)

    def test_probe_engine_heartbeat_stale_write(self):
        long_ago = datetime.datetime.now() - datetime.timedelta(hours=2)
        with patch("core.trade_db.get_trade_journal", return_value=[
            {"exit_time": long_ago.strftime("%Y-%m-%d %H:%M:%S")}
        ]):
            icon, text = self.tg_bot._probe_engine_heartbeat("paper", stale_minutes=30)
        self.assertEqual(icon, "🟠")
        self.assertIn("Stalled", text)

    def test_probe_engine_heartbeat_db_error(self):
        with patch("core.trade_db.get_trade_journal", side_effect=RuntimeError("db gone")):
            icon, text = self.tg_bot._probe_engine_heartbeat("paper")
        self.assertEqual(icon, "🔴")
        self.assertIn("Unreachable", text)

    def test_probe_market_status_open(self):
        with patch("core.market_calendar.is_market_open", return_value=True):
            icon, text = self.tg_bot._probe_market_status(self.tg_bot.CONFIG)
        self.assertEqual(icon, "🟢")
        self.assertIn("Open", text)

    def test_probe_market_status_closed(self):
        with patch("core.market_calendar.is_market_open", return_value=False), \
             patch("core.market_calendar.get_seconds_until_market_open", return_value=3600):
            icon, text = self.tg_bot._probe_market_status(self.tg_bot.CONFIG)
        self.assertEqual(icon, "🔴")
        self.assertIn("Closed", text)
        self.assertIn("reopens at", text)

    # --- set_my_commands for slash suggestions --------------------------

    def test_bot_command_list_includes_all_public_commands(self):
        commands = {c.command for c in self.tg_bot.BOT_COMMAND_LIST}
        # All user-facing commands must be in the suggestion list.
        for cmd in ("start", "stop", "pnl", "positions", "status", "summary", "help"):
            self.assertIn(cmd, commands, f"/{cmd} missing from BOT_COMMAND_LIST")
        # Owner-only admin commands are intentionally NOT advertised to all
        # users (they wouldn't be useful as inline suggestions anyway).
        self.assertNotIn("subscribers", commands)
        self.assertNotIn("ban", commands)

    def test_post_init_registers_commands_with_telegram(self):
        # The Application.post_init hook should call set_my_commands so that
        # typing "/" in the chat surfaces inline suggestions.
        app = MagicMock()
        app.bot.set_my_commands = AsyncMock()
        _run(self.tg_bot._post_init(app))
        app.bot.set_my_commands.assert_awaited_once()
        registered = app.bot.set_my_commands.await_args.args[0]
        self.assertEqual(len(registered), len(self.tg_bot.BOT_COMMAND_LIST))
        names = {c.command for c in registered}
        self.assertIn("pnl", names)
        self.assertIn("summary", names)

    def test_post_init_swallows_telegram_api_errors(self):
        # A failure to register (network blip, etc.) must not stop the bot.
        app = MagicMock()
        app.bot.set_my_commands = AsyncMock(side_effect=RuntimeError("network"))
        # Should not raise.
        _run(self.tg_bot._post_init(app))

    # --- /summary builder -----------------------------------------------

    def test_summary_text_empty_journal(self):
        with patch.object(self.tg_bot, "get_trade_journal", return_value=[]):
            text = self.tg_bot._build_summary_text()
        self.assertIn("STRATEGY LIFETIME SUMMARY", text)
        self.assertIn("No trades recorded yet", text)

    def test_summary_text_aggregates_wins_and_profit_factor(self):
        journal = [
            {"net_pnl": 500.0},
            {"net_pnl": 1000.0},
            {"net_pnl": -200.0},
            {"net_pnl": -300.0},
        ]
        with patch.object(self.tg_bot, "get_trade_journal", return_value=journal):
            text = self.tg_bot._build_summary_text()
        self.assertIn("Total Trades: 4", text)
        self.assertIn("Wins / Losses: 2 / 2", text)
        self.assertIn("Win Rate: 50.00%", text)
        # gross_gains=1500, gross_losses=500 → profit_factor=3.00
        self.assertIn("Profit Factor: 3.00", text)
        # net = 1500 - 500 = 1000
        self.assertIn("Net Profit: +₹1,000.00", text)

    def test_summary_text_profit_factor_uses_infinity_when_no_losses(self):
        with patch.object(self.tg_bot, "get_trade_journal", return_value=[
            {"net_pnl": 500.0},
            {"net_pnl": 1000.0},
        ]):
            text = self.tg_bot._build_summary_text()
        self.assertIn("Profit Factor: ∞", text)
        # Make sure the old sentinel 999.99 is gone.
        self.assertNotIn("999.99", text)

    def test_summary_text_profit_factor_dash_when_no_trades(self):
        with patch.object(self.tg_bot, "get_trade_journal", return_value=[
            {"net_pnl": 0.0},
            {"net_pnl": 0.0},
        ]):
            text = self.tg_bot._build_summary_text()
        # All-zero trades: gross_gains==0, gross_losses==0 → em-dash.
        self.assertIn("Profit Factor: —", text)

    # --- access control --------------------------------------------------

    def test_unauthorized_chat_is_prompted_for_invite(self):
        update = _make_update(chat_id=999999)  # not subscribed, not banned
        with patch.object(self.tg_bot, "_build_pnl_text", return_value="(should not see this)"):
            _run(self.tg_bot.cmd_pnl(update, MagicMock()))
        update.message.reply_text.assert_awaited_once()
        reply = update.message.reply_text.await_args.args[0]
        self.assertIn("🔒", reply)
        self.assertIn("invite", reply.lower())

    def test_banned_chat_is_silently_dropped(self):
        chat_id = 888888
        # mark_banned is an UPDATE; we need an existing row first.
        self.registry.start_invite(chat_id, "spammer", "Spammer")
        self.registry.mark_banned(chat_id)
        update = _make_update(chat_id=chat_id)
        with patch.object(self.tg_bot, "_build_pnl_text", return_value="(should not see this)"):
            _run(self.tg_bot.cmd_pnl(update, MagicMock()))
        update.message.reply_text.assert_not_awaited()

    def test_active_subscriber_sees_pnl(self):
        update = _make_update(chat_id=111)  # alice is subscribed
        with patch.object(self.tg_bot, "_build_pnl_text", return_value="📊 PNL CONTENT"):
            _run(self.tg_bot.cmd_pnl(update, MagicMock()))
        update.message.reply_text.assert_awaited_once_with(
            "📊 PNL CONTENT", parse_mode="Markdown"
        )

    def test_owner_always_passes_access_check(self):
        os.environ["TELEGRAM_OWNER_CHAT_ID"] = "42"
        update = _make_update(chat_id=42)  # owner, not in subscribers table
        with patch.object(self.tg_bot, "_build_status_text", return_value="🏥 STATUS"):
            _run(self.tg_bot.cmd_status(update, MagicMock()))
        update.message.reply_text.assert_awaited_once_with("🏥 STATUS", parse_mode="Markdown")

    def test_active_subscriber_sees_positions(self):
        update = _make_update(chat_id=222)
        with patch.object(self.tg_bot, "_build_positions_text", return_value="⚡ POSITIONS"):
            _run(self.tg_bot.cmd_positions(update, MagicMock()))
        update.message.reply_text.assert_awaited_once_with(
            "⚡ POSITIONS", parse_mode="Markdown"
        )

    def test_active_subscriber_sees_summary(self):
        update = _make_update(chat_id=111)
        with patch.object(self.tg_bot, "_build_summary_text", return_value="📈 SUMMARY"):
            _run(self.tg_bot.cmd_summary(update, MagicMock()))
        update.message.reply_text.assert_awaited_once_with(
            "📈 SUMMARY", parse_mode="Markdown"
        )

    # --- main() registration --------------------------------------------

    def test_main_registers_new_command_handlers(self):
        with patch.object(self.tg_bot, "ApplicationBuilder") as builder:
            app = MagicMock()
            # main() now chains: ApplicationBuilder().token(t).post_init(cb).build()
            # So we need each link in the chain to return something that
            # supports the next call.
            chain = MagicMock()
            chain.post_init.return_value = chain
            chain.build.return_value = app
            builder.return_value.token.return_value = chain
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_INVITE_CODE": "y"}):
                self.tg_bot.main()
        registered: set = set()
        for call in app.add_handler.call_args_list:
            handler = call.args[0]
            for cmd in getattr(handler, "commands", []) or []:
                registered.add(cmd)
        for cmd in ("start", "stop", "help", "status", "pnl", "positions", "summary", "subscribers"):
            self.assertIn(cmd, registered, f"CommandHandler for /{cmd} not registered")
        # post_init must have been wired to the builder (used to register
        # the slash command list with Telegram).
        self.assertTrue(chain.post_init.called, "post_init hook not wired into ApplicationBuilder")


if __name__ == "__main__":
    unittest.main()
