# 🚀 Cloud Execution Setup Guide (GitHub Actions & Telegram)

This guide explains how to run your trading daemon **100% free in the cloud** using GitHub Actions, while keeping your personal credentials completely private in your personal fork.

---

## 🛠️ Step 1: Create a Personal Telegram Bot (For Mobile Alerts)

1. Open **Telegram** on your mobile or desktop and search for **`@BotFather`**.
2. Send `/newbot` and follow the prompts to choose a name and username (e.g. `MyShoonyaTradingBot`).
3. BotFather will provide an **HTTP API Token** (e.g. `7182938491:AAHqj_...`). Copy this token.
4. Generate a strong **invite code** that you'll share with paying subscribers, e.g.:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(16))"
   ```
   Anyone who knows this code can DM the bot and subscribe. Keep it private.
5. *(Optional, for the single-user legacy path)* Search for **`@userinfobot`** on Telegram, send `/start`, and copy your numeric **Id** (e.g. `123456789`).

---

## 🍴 Step 2: Fork the Repository

1. Navigate to the main repository: `https://github.com/vaibhavreddys/algotradingdaily`
2. Click the **"Fork"** button in the top-right corner.
3. Choose your personal GitHub account as the destination.
4. Uncheck *"Copy the main branch only"* (so you have access to all branches).
5. Click **"Create fork"**.

---

## 🔒 Step 3: Add Private Secrets to Your Personal Fork

In your **personal forked repository** on GitHub:

1. Go to **Settings** $\to$ **Secrets and variables** $\to$ **Actions**.
2. Under the **Secrets** tab, click **"New repository secret"** for each:
   - **Name**: `TELEGRAM_BOT_TOKEN` — *the token from BotFather*
   - **Name**: `TELEGRAM_INVITE_CODE` — *the invite code you generated*
   - **Name**: `TELEGRAM_CHAT_ID` — *(Optional, legacy single-user path)* your own numeric chat_id
   - **Name**: `TELEGRAM_OWNER_CHAT_ID` — *(Optional)* your own numeric chat_id; enables admin commands in your chat
3. *(Optional - Only needed when switching to live money)*:
   - `SHOONYA_USER`, `SHOONYA_PWD`, `SHOONYA_API_KEY`, `SHOONYA_VENDOR_CODE`, `SHOONYA_TOTP_KEY`, `SHOONYA_IMEI`.

> The GitHub Actions workflow only runs the trading engine, not the bot's long-polling worker. To allow new subscribers to `/start` and receive the invite code, run `python -m alerts.tg_bot` on a long-lived host (your Oracle Cloud VPS — see the README). On Actions, the engine still broadcasts to whoever is in the subscribers DB, including the auto-seeded `TELEGRAM_CHAT_ID`.

---

## 🏃 Step 4: Run & Test the Cloud Bot

In your **personal forked repository**:

1. Click the **"Actions"** tab at the top.
2. If Actions are disabled on your fork, click **"I understand my workflows, go ahead and enable them"**.
3. In the left sidebar, click **"Daily Paper Trading Execution Daemon"**.
4. Click the **"Run workflow"** button on the right $\to$ select branch `main` (or `development`) $\to$ click **"Run workflow"**.
5. Click on the running job to watch live terminal logs.
6. Verify you receive a test startup / entry notification on your Telegram app!

---

## ⏰ Step 5: Automated Daily Schedule

Once enabled in your fork:
- The cloud daemon runs **automatically Monday to Friday at 09:10 AM IST (03:40 UTC)**.
- It scans the Nifty 50 universe, trails Stop-Losses, enforces 3:00 PM auto-squareoffs, and sends you instant Telegram alerts.
- At 15:35 IST, it saves your `paper_trades.db` SQLite database as a GitHub Artifact and shuts down gracefully.

---

## 🤖 Step 6: Subscriber & Owner Bot Commands

The Telegram bot is user-agnostic — anyone with the **invite code** can subscribe and start receiving alerts.

### Subscriber flow (end user)
1. Open the bot in Telegram and send `/start`.
2. The bot replies: *"To start receiving live trade alerts, send me your invite code now."*
3. Send the invite code in your next message. On match, you get a welcome message and start receiving alerts immediately.
4. A wrong code lets you try again; a second wrong code **bans** the chat. (Owner can unban via `/reinstate <chat_id>`.)
5. Available commands: `/start`, `/stop`, `/status`, `/help`.

### Owner flow (admin commands, only in your own chat)
Set `TELEGRAM_OWNER_CHAT_ID` to your numeric chat_id and these become available in your chat with the bot:
- `/subscribers` — list every active subscriber (chat_id, username, source, subscribed_at).
- `/pending` — list chat_ids that have begun `/start` but haven't verified the invite code yet.
- `/revoke <chat_id>` — deactivate a subscriber (e.g. subscription cancelled). They can `/start` again to rejoin.
- `/reinstate <chat_id>` — re-activate a banned or revoked chat_id without an invite code.
- `/ban <chat_id>` — hard block. The user cannot subscribe even with the correct invite code.

### Where the bot runs
- **On the VPS**: `scripts/run_daily_algo.sh` now launches the bot as a background process alongside the engine and tears it down on exit. As long as the engine runs on a weekday, the bot is reachable.
- **24/7 (so people can `/start` on weekends)**: add a separate cron entry: `@reboot /home/ubuntu/trading/algotradingdaily/venv/bin/python -u -m alerts.tg_bot >> telegram_bot_output.log 2>&1`.
- **On GitHub Actions**: the bot process is **not** started. Actions workflows are ephemeral (~6 h/weekday) and the seeded `TELEGRAM_CHAT_ID` keeps the owner's chat active during the run.

---

## 🔄 Keeping Your Fork Updated With New Features

Whenever new strategies, bug fixes, or improvements are committed to `vaibhavreddys/algotradingdaily`:
1. Open your personal fork on GitHub.
2. Click the **"Sync fork"** button at the top $\to$ click **"Update branch"**.
3. Your fork is updated to the latest code in 1 second!
