"""
Telegram Bot — long-polling worker for /start subscribe / /stop unsubscribe,
owner-only admin commands, and subscriber-only on-demand portfolio queries
(/pnl, /positions, /status, /summary).

Run as a separate process so the trading engine's main loop is never blocked:

    python -m alerts.tg_bot

Required env:
    TELEGRAM_BOT_TOKEN    Bot token from @BotFather
    TELEGRAM_INVITE_CODE  Shared secret given to subscribers

Optional env:
    TELEGRAM_OWNER_CHAT_ID  Owner's numeric chat_id. If set, owner-only
                            admin commands (subscribers / pending / revoke /
                            reinstate / ban) become available in that chat.
    TELEGRAM_CHAT_ID        Backward-compat: if set, this chat_id is
                            auto-seeded as an active subscriber on startup.
"""

import os
import sys
from dotenv import load_dotenv
load_dotenv()
import logging
import datetime
import requests
from typing import Optional, List, Dict, Any, Tuple

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import CONFIG, TradingConfig
from alerts.subscribers import SubscribersRegistry, get_db_connection
from core.trade_db import (
    get_active_positions,
    get_trade_journal,
    get_today_realized_pnl,
)


logging.basicConfig(
    format="%(asctime)s [TG-BOT] %(levelname)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("alerts.tg_bot")

WELCOME = (
    "👋 Welcome to AlgoTradingDaily!\n\n"
    "To start receiving live trade alerts, send me your invite code now."
)
WELCOME_BACK = "Welcome back! Send your invite code to reactivate your subscription."
HELP_USER = (
    "🤖 *AlgoTradingDaily Bot*\n\n"
    "User commands:\n"
    "• /start — begin the invite-code flow\n"
    "• /stop  — unsubscribe from alerts\n"
    "• /pnl — today's realized + open MTM P&L scorecard\n"
    "• /positions — currently open trades with trailing status\n"
    "• /status — gateway heartbeat, broker session, and engine mode\n"
    "• /summary — strategy lifetime journal (wins, profit factor, ROI)\n"
    "• /help  — show this message"
)
HELP_OWNER = (
    "\n\nOwner commands (only in the owner's chat):\n"
    "• /subscribers — list active subscribers\n"
    "• /pending — list chat_ids awaiting invite code\n"
    "• /revoke \\<chat_id\\> — deactivate a subscriber\n"
    "• /reinstate \\<chat_id\\> — re-activate without an invite code\n"
    "• /ban \\<chat_id\\> — hard block a chat_id"
)


def _owner_chat_id() -> Optional[int]:
    raw = os.getenv("TELEGRAM_OWNER_CHAT_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        log.warning("TELEGRAM_OWNER_CHAT_ID=%r is not numeric; owner commands disabled.", raw)
        return None


def _is_owner(update: Update) -> bool:
    owner = _owner_chat_id()
    if owner is None:
        return False
    return update.effective_chat is not None and update.effective_chat.id == owner


async def _require_subscriber(update: Update) -> bool:
    """
    Gates an on-demand command. Returns True if the caller may proceed, after
    also sending a one-line "send /start + invite code" nudge when the chat is
    not subscribed and not banned. Banned chats are silently dropped to match
    the existing free-text flow. The configured owner is always allowed.
    """
    chat = update.effective_chat
    if chat is None:
        return False
    registry = SubscribersRegistry()
    if registry.is_banned(chat.id):
        return False
    if _is_owner(update) or registry.is_active(chat.id):
        return True
    if update.message is not None:
        await update.message.reply_text(
            "🔒 This command is for active subscribers only. "
            "Send /start and your invite code to subscribe."
        )
    return False


# --- command handlers ------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    registry = SubscribersRegistry()
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return

    if registry.is_banned(chat.id):
        await update.message.reply_text("This chat is blocked from the bot.")
        return

    if registry.is_active(chat.id):
        await update.message.reply_text(
            "You're already subscribed. Use /stop to unsubscribe, /help for commands."
        )
        return

    registry.start_invite(chat.id, user.username, user.first_name)
    await update.message.reply_text(WELCOME)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    registry = SubscribersRegistry()
    chat = update.effective_chat
    if chat is None:
        return
    if registry.unsubscribe(chat.id):
        await update.message.reply_text("Unsubscribed. You will no longer receive alerts. /start to rejoin.")
    else:
        await update.message.reply_text("You weren't subscribed.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = HELP_USER + (HELP_OWNER if _is_owner(update) else "")
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_subscriber(update):
        return
    await update.message.reply_text(_build_status_text(), parse_mode="Markdown")


# --- subscriber-only on-demand portfolio commands -------------------------


# --- synchronous text builders (testable without the bot) -----------------


def _safe_ltp(ticker: str) -> Optional[Dict[str, Any]]:
    """Returns {ltp, high, low} from fetch_latest_tick_price or None on any failure."""
    try:
        from data_pipeline import fetch_latest_tick_price
        tick = fetch_latest_tick_price(ticker)
        if not tick:
            return None
        return tick
    except Exception:
        return None


def _build_pnl_text(mode: str = "paper", config: TradingConfig = CONFIG) -> str:
    """Today's realized + best-effort open MTM P&L scorecard."""
    today_prefix = datetime.datetime.now().strftime("%Y-%m-%d")
    try:
        realized = get_today_realized_pnl(mode=mode)
    except Exception as e:
        return f"⚠️ Could not read trade database: `{e}`"

    try:
        today_trades = [
            t for t in get_trade_journal(mode=mode, limit=500)
            if str(t.get("exit_time", "")).startswith(today_prefix)
        ]
    except Exception as e:
        return f"⚠️ Could not read trade journal: `{e}`"

    wins = sum(1 for t in today_trades if float(t.get("net_pnl", 0)) > 0)
    losses = sum(1 for t in today_trades if float(t.get("net_pnl", 0)) <= 0)

    # Open MTM: best-effort, only counted when we can actually fetch a tick.
    open_mtm_total = 0.0
    open_mtm_known = 0
    open_mtm_unknown = 0
    try:
        active_rows = get_active_positions(mode=mode)
    except Exception:
        active_rows = []
    for row in active_rows:
        symbol = row.get("symbol", "")
        clean = symbol.replace("-EQ", "").replace(".NS", "")
        ticker = f"{clean}.NS" if not clean.endswith(".NS") else clean
        tick = _safe_ltp(ticker)
        if not tick or tick.get("ltp") is None:
            open_mtm_unknown += 1
            continue
        ltp = float(tick["ltp"])
        entry = float(row.get("entry_price", 0.0))
        qty = int(row.get("quantity", 0))
        # Strategy is short-side, so MTM = (entry - ltp) * qty.
        open_mtm_total += (entry - ltp) * qty
        open_mtm_known += 1

    # Ending capital: latest balance_after_trade if present, else starting capital + realized.
    ending_capital = config.INITIAL_CAPITAL + realized
    if today_trades:
        last = max(today_trades, key=lambda t: t.get("id", 0))
        bal = last.get("balance_after_trade")
        if bal is not None:
            ending_capital = float(bal)

    total_today = realized + open_mtm_total
    total_pct = (total_today / config.INITIAL_CAPITAL * 100.0) if config.INITIAL_CAPITAL > 0 else 0.0
    mtm_note = f" (across {open_mtm_known} open)" if open_mtm_known else ""
    if open_mtm_unknown and not open_mtm_known:
        mtm_note = " (n/a — live tick unavailable)"

    return (
        f"📊 *[TODAY P&L SCORECARD]*\n"
        f"• Date: `{today_prefix}`\n"
        f"• Realized P&L: {'+' if realized >= 0 else '-'}₹{abs(realized):,.2f}\n"
        f"• Open MTM P&L: {'+' if open_mtm_total >= 0 else '-'}₹{abs(open_mtm_total):,.2f}{mtm_note}\n"
        f"• Total Today: {'+' if total_today >= 0 else '-'}₹{abs(total_today):,.2f} "
        f"({total_pct:+.2f}%)\n"
        f"• Total Trades: {len(today_trades)} ({wins} Wins, {losses} Losses)\n"
        f"• Ending Capital: ₹{ending_capital:,.2f}"
    )


def _build_positions_text(mode: str = "paper") -> str:
    """Currently active open positions with trailing status + best-effort MTM."""
    try:
        active = get_active_positions(mode=mode)
    except Exception as e:
        return f"⚠️ Could not read active positions: `{e}`"

    if not active:
        return "💤 No open positions currently active."

    lines: List[str] = []
    for i, pos in enumerate(active, 1):
        symbol = pos.get("symbol", "?")
        clean = symbol.replace("-EQ", "").replace(".NS", "")
        ticker = f"{clean}.NS" if not clean.endswith(".NS") else clean
        qty = int(pos.get("quantity", 0))
        entry = float(pos.get("entry_price", 0.0))
        sl = float(pos.get("current_sl", 0.0))
        tp = float(pos.get("target_price", 0.0))

        if sl <= entry and sl > 0:
            sl_note = f"₹{sl:,.2f} (🛡️ Trailed Breakeven)"
        else:
            sl_note = f"₹{sl:,.2f}"

        mtm_line = "• Est. MTM: n/a (tick unavailable)"
        tick = _safe_ltp(ticker)
        if tick and tick.get("ltp") is not None:
            ltp = float(tick["ltp"])
            # Strategy is short-side, so a price drop = profit.
            mtm = (entry - ltp) * qty
            mtm_pct = (mtm / (entry * qty) * 100.0) if entry * qty else 0.0
            mtm_line = f"• Est. MTM: {'+' if mtm >= 0 else '-'}₹{abs(mtm):,.2f} ({mtm_pct:+.2f}%)"

        lines.append(
            f"{i}. `{clean}` (SHORT)\n"
            f"   • Qty: {qty} | Entry: ₹{entry:,.2f}\n"
            f"   • Current SL: {sl_note}\n"
            f"   • Target: ₹{tp:,.2f} (1:2 R:R)\n"
            f"   {mtm_line}"
        )

    return "⚡ *[ACTIVE OPEN POSITIONS]*\n" + "\n".join(lines)


def _probe_openalgo_gateway(host: str, timeout: float = 3.0) -> Tuple[str, str]:
    """
    Returns (icon, status_text) for the OpenAlgo gateway heartbeat. Tries
    a cheap HTTP GET on the host root; treats any reachable response as online
    (including 4xx, since the gateway is up even if the path needs auth).
    """
    try:
        res = requests.get(host, timeout=timeout)
        return "🟢", f"Online ({host})"
    except requests.exceptions.ConnectionError:
        return "🔴", f"Offline — connection refused at {host}"
    except requests.exceptions.Timeout:
        return "🟠", f"Timeout reaching {host}"
    except Exception as e:
        return "🟠", f"Unreachable ({type(e).__name__}: {e})"


def _probe_broker_session(mode: str) -> Tuple[str, str]:
    """Returns (icon, status_text) for broker session. Paper mode always reports N/A."""
    if mode != "live":
        return "🟡", "Skipped (paper mode)"

    host = getattr(CONFIG, "OPENALGO_HOST", "http://127.0.0.1:5000")
    api_key = getattr(CONFIG, "OPENALGO_API_KEY", "") or os.getenv("OPENALGO_API_KEY", "")
    try:
        from openalgo import api as OpenAlgoClient
    except ImportError:
        return "🟠", "openalgo SDK not importable"

    try:
        client = OpenAlgoClient(api_key=api_key, host=host)
        limits = client.get_limits() or {}
        stat = (limits.get("stat") or "").lower()
        if stat == "ok":
            return "🟢", "Active (broker session valid)"
        msg = limits.get("message") or limits.get("error") or "Unknown"
        if "session" in str(msg).lower() and ("expir" in str(msg).lower() or "invalid" in str(msg).lower()):
            return "🔴", f"Expired — {msg}"
        return "🟠", f"Degraded — {msg}"
    except Exception as e:
        return "🔴", f"Unreachable ({type(e).__name__}: {e})"


def _build_status_text(config: TradingConfig = CONFIG) -> str:
    """Gateway + broker + engine heartbeat."""
    host = getattr(config, "OPENALGO_HOST", "http://127.0.0.1:5000")
    gw_icon, gw_text = _probe_openalgo_gateway(host)
    br_icon, br_text = _probe_broker_session(config.TRADING_MODE)

    universe = (config.UNIVERSE or "NIFTY50").upper()
    universe_count: Optional[int] = None
    try:
        from data_pipeline.data_feed import get_symbols_for_universe
        universe_count = len(get_symbols_for_universe(universe))
    except Exception:
        universe_count = None

    universe_line = f"{universe} ({universe_count} Constituents)" if universe_count else universe

    next_scan = "outside trading hours"
    try:
        from core.market_calendar import is_market_open as _is_open, get_seconds_until_next_candle as _secs_next
        if _is_open(getattr(config, "EXCHANGE", "NSE")):
            secs = _secs_next(interval_mins=15)
            next_scan = (datetime.datetime.now() + datetime.timedelta(seconds=secs)).strftime("%H:%M:%S")
            next_scan = f"{next_scan} IST"
    except Exception:
        pass

    return (
        f"🏥 *[SYSTEM STATUS]*\n"
        f"• Mode: `{config.TRADING_MODE.upper()} TRADING`\n"
        f"• Gateway: {gw_icon} OpenAlgo {gw_text}\n"
        f"• Broker Session: {br_icon} {br_text}\n"
        f"• Universe: {universe_line}\n"
        f"• Next 15m Scan: {next_scan}"
    )


def _build_summary_text(mode: str = "paper") -> str:
    """Strategy lifetime journal: wins, losses, win rate, profit factor, net PnL."""
    try:
        journal = get_trade_journal(mode=mode, limit=50_000)
    except Exception as e:
        return f"⚠️ Could not read trade journal: `{e}`"

    if not journal:
        return "📈 *[STRATEGY LIFETIME SUMMARY]*\n• No trades recorded yet."

    total = len(journal)
    wins = [t for t in journal if float(t.get("net_pnl", 0)) > 0]
    losses = [t for t in journal if float(t.get("net_pnl", 0)) <= 0]
    win_rate = (len(wins) / total) * 100.0 if total else 0.0

    gross_gains = sum(float(t.get("net_pnl", 0)) for t in wins)
    gross_losses = sum(abs(float(t.get("net_pnl", 0))) for t in losses)
    profit_factor = (gross_gains / gross_losses) if gross_losses > 0 else 999.99
    total_net = gross_gains - gross_losses

    return (
        f"📈 *[STRATEGY LIFETIME SUMMARY]*\n"
        f"• Total Trades: {total}\n"
        f"• Wins / Losses: {len(wins)} / {len(losses)}\n"
        f"• Win Rate: {win_rate:.2f}%\n"
        f"• Profit Factor: {profit_factor:.2f}\n"
        f"• Net Profit: {'+' if total_net >= 0 else '-'}₹{abs(total_net):,.2f}"
    )


# --- on-demand command handlers -------------------------------------------


async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_subscriber(update):
        return
    await update.message.reply_text(_build_pnl_text(mode=CONFIG.TRADING_MODE), parse_mode="Markdown")


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_subscriber(update):
        return
    await update.message.reply_text(_build_positions_text(mode=CONFIG.TRADING_MODE), parse_mode="Markdown")


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_subscriber(update):
        return
    await update.message.reply_text(_build_summary_text(mode=CONFIG.TRADING_MODE), parse_mode="Markdown")


# --- owner-only admin handlers ---------------------------------------------


async def cmd_subscribers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    rows = SubscribersRegistry().list_active()
    if not rows:
        await update.message.reply_text("No active subscribers.")
        return
    lines = ["*Active subscribers:*"]
    for r in rows:
        handle = f"@{r['username']}" if r["username"] else "(no username)"
        name = r["first_name"] or ""
        lines.append(
            f"• `{r['chat_id']}` — {name} {handle} — {r['source']} — {r['subscribed_at']}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    pending = SubscribersRegistry().pending_chat_ids()
    if not pending:
        await update.message.reply_text("No pending invite flows.")
        return
    await update.message.reply_text(
        "Pending chat_ids:\n" + "\n".join(f"• `{cid}`" for cid in pending),
        parse_mode="Markdown",
    )


async def _admin_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str) -> None:
    if not _is_owner(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(f"Usage: /{action} <chat_id>")
        return
    target = int(context.args[0])
    registry = SubscribersRegistry()
    if action == "revoke":
        ok = registry.unsubscribe(target)
        msg = f"Revoked `{target}`." if ok else f"`{target}` was not active."
    elif action == "reinstate":
        ok = registry.reinstate(target)
        msg = f"Reinstated `{target}`." if ok else f"`{target}` not found."
    elif action == "ban":
        ok = registry.mark_banned(target)
        msg = f"Banned `{target}`." if ok else f"`{target}` not found."
    else:
        msg = "Unknown action."
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _admin_id_command(update, context, "revoke")


async def cmd_reinstate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _admin_id_command(update, context, "reinstate")


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _admin_id_command(update, context, "ban")


# --- free-text handler: invite-code verification ----------------------------


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all for non-command text. Used to verify invite codes during /start."""
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None or update.message is None:
        return

    registry = SubscribersRegistry()

    if registry.is_banned(chat.id):
        return

    if registry.is_active(chat.id):
        # Already subscribed — don't try to interpret arbitrary chatter as an invite code.
        return

    code = (update.message.text or "").strip()
    if not code:
        return

    if registry.verify_invite(chat.id, code):
        await update.message.reply_text(
            "✅ Invite accepted. You are now subscribed to AlgoTradingDaily alerts.\n"
            "Use /stop to unsubscribe, /help for commands."
        )
    else:
        # Look up how many attempts remain to tailor the message.
        attempts = 0
        is_banned = False
        try:
            with get_db_connection() as conn:
                row = conn.execute(
                    "SELECT pending_code_attempts, banned FROM subscribers WHERE chat_id = ?",
                    (chat.id,),
                ).fetchone()
            if row is not None:
                attempts = int(row["pending_code_attempts"])
                is_banned = bool(row["banned"])
        except Exception:
            is_banned = False
        if is_banned:
            await update.message.reply_text("Too many incorrect attempts. This chat is now blocked.")
            return
        remaining = max(0, 2 - attempts)
        await update.message.reply_text(
            f"❌ Incorrect invite code. {remaining} attempt(s) remaining before this chat is blocked."
        )


# --- entry point -----------------------------------------------------------


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    invite = os.getenv("TELEGRAM_INVITE_CODE", "").strip()
    if not token:
        log.error("TELEGRAM_BOT_TOKEN is not set; cannot start the bot.")
        return 1
    if not invite:
        log.error("TELEGRAM_INVITE_CODE is not set; refusing to start the bot.")
        return 1

    registry = SubscribersRegistry()
    if registry.seed_env_chat_id():
        log.info("Seeded TELEGRAM_CHAT_ID into subscribers table.")

    owner = _owner_chat_id()
    if owner is not None:
        log.info("Owner chat_id configured: %s", owner)
    else:
        log.info("TELEGRAM_OWNER_CHAT_ID not set; owner admin commands disabled.")

    log.info("Starting long-polling Telegram bot at %s", datetime.datetime.now().isoformat(timespec="seconds"))

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("pnl", cmd_pnl))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("subscribers", cmd_subscribers))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("reinstate", cmd_reinstate))
    app.add_handler(CommandHandler("ban", cmd_ban))
    # Free-text handler last so commands win.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
