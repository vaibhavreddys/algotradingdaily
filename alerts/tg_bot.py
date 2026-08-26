"""
Telegram Bot — long-polling worker for /start subscribe / /stop unsubscribe
and owner-only admin commands.

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
import logging
import datetime
from typing import Optional

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

from config import CONFIG
from alerts.subscribers import SubscribersRegistry, get_db_connection


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
    "• /status — show the bot's current trading mode and universe\n"
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
    text = (
        f"ℹ️ *Bot status*\n"
        f"• Trading mode: `{CONFIG.TRADING_MODE}`\n"
        f"• Universe: `{CONFIG.UNIVERSE}`\n"
        f"• Exchange: `{CONFIG.EXCHANGE_MARKET}`\n"
        f"• Order type: `{CONFIG.ORDER_TYPE}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


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
