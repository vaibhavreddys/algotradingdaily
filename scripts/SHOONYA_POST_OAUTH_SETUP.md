# Shoonya post-OAuth setup guide (for replicating this VM's setup)

Reference implementation lives on the original VM; this checklist gets a
friend from zero to the same working state. Two pieces of software share the
broker session: **OpenAlgo** (holds the OAuth session token in its DB) and
**algotradingdaily** (reads that token for MCX data ingestion). One
Shoonya/OAuth app per person — never share client_id/secret/passwords.

---

## A. Finvasia / Shoonya portal (one-time, manual)

1. Create an **OAuth API app** on Finvasia's API portal. You get a
   `client_id` (looks like `FA123456_U`) and a `secret_key`.
2. Register the **redirect URI**: `https://<your-public-domain>/auth/shoonya/callback`
   (this is where the single-use auth code lands after each login).
3. **Whitelist your VM's static IP** on the API app (SEBI mandate, enforced
   since 2026-04-01). Requests from non-whitelisted IPs are rejected.
4. Enable **TOTP 2FA** on the Shoonya account and keep the Base32 seed —
   the auto-login generates codes from it (`pyotp`).
5. Legacy notes: `/NorenWClientTP/` is decommissioned (502), vendor
   `QuickAuth` rejects every vendor-code shape ("Invalid Vendor code") and
   there is **no token-renewal endpoint**. Sessions can only be minted via
   the OAuth login flow.

## B. OpenAlgo on the VM

1. Clone the repo to `$HOME/openalgo`, create its venv (`.venv`), install
   `requirements.txt`.
2. `openalgo/.env` must contain at least:
   - `BROKER_API_KEY = '<userid>:::<client_id>'`
   - `BROKER_API_SECRET = '<secret_key>'`
   - `API_KEY_PEPPER` (32+ chars) and `FERNET_SALT` (64 hex chars) — these
     derive the Fernet key that encrypts the stored session token
   - `HOST_SERVER = 'https://<your-public-domain>'`, `FLASK_PORT = '5000'`
3. Start it once and **log in via the web UI** (Shoonya OAuth) so the
   `auth` table row is created.
4. Install as a systemd service:
   `bash scripts/install_openalgo_service.sh` (patches `app.py` with
   `allow_unsafe_werkzeug=True` and writes `openalgo.service`).

## C. Headless daily auto-login (`openalgo/auto_login_shoonya/`)

1. Copy the folder from this repo; then create `auto_login_shoonya/.env`:
   - `SHOONYA_USER_ID`, `SHOONYA_PASSWORD` (plaintext — the OAuth form
     needs it), `SHOONYA_TOTP_SECRET` (Base32 seed)
   - `OPENALGO_DIR=/home/<user>/openalgo`
   - `OPENALGO_API_KEY` (generate at `/apikey` in the OpenAlgo web UI —
     used for the end-to-end funds check)
   - `NOTIFICATION_WEBHOOK=https://api.telegram.org/bot<TOKEN>/sendMessage`
     and `NOTIFICATION_CHAT_ID` for Telegram status messages
2. Browser automation (Selenium is NOT usable on ARM64 VMs — its bundled
   selenium-manager is x86_64-only; use Playwright):
   ```
   .venv/bin/pip install playwright
   .venv/bin/playwright install chromium
   sudo .venv/bin/playwright install-deps chromium
   ```
3. Test: `.venv/bin/python auto_login_shoonya/auto_login_shoonya.py --force`
   then plain (idempotent no-op). Exit 0 + Telegram message = done.
4. Cron (system clock is UTC; `30 0 * * *` = 06:00 IST daily, no-op when the
   session is still valid):
   ```
   30 0 * * * /home/<user>/openalgo/.venv/bin/python /home/<user>/openalgo/auto_login_shoonya/auto_login_shoonya.py >> /home/<user>/openalgo/auto_login_shoonya/cron.log 2>&1
   ```

## D. MCX 1-minute data pipeline (`algotradingdaily`)

1. `pip install -r requirements.txt` into `venv/` (includes `cryptography`
   for decrypting OpenAlgo's stored token).
2. `.env` needs `SHOONYA_USER_ID` (plain user id, NOT the `uid:::client_id`
   string) and `OPENALGO_DIR` pointing at the OpenAlgo checkout.
3. Verify then download:
   ```
   venv/bin/python scripts/shoonya_mcx_test_auth.py     # preflight
   venv/bin/python -m data_pipeline.shoonya_mcx --action download
   venv/bin/python -m data_pipeline.shoonya_mcx --action stats
   ```
   Tokens resolve from Shoonya's official MCX scrip master (SearchScrip
   answers "Exchange Not enabled" for MCX on retail OAuth apps); sessions
   come from OpenAlgo's DB, so the 06:00 IST auto-login covers both systems.

## E. tmux status bar (optional)

Copy `scripts/tmux_openalgo_status.sh` to `~/bin/` and add to `~/.tmux.conf`:

```
set -g status-interval 15
set -g status-right "#($HOME/bin/tmux_openalgo_status.sh) #[fg=colour240]#h #[fg=colour37]%d %b %H:%M#[default]"
```

Green `OA:UP` = systemd active + HTTP 200 on :5000; red `OA:DOWN` = service
dead; orange `OA:<http_code>` = running but web not answering.

---

### Security notes

- Never commit or share: passwords, TOTP seeds, `BROKER_API_SECRET`,
  `API_KEY_PEPPER`, `FERNET_SALT`, Telegram tokens, API keys. Every `.env`
  is gitignored in both repos — keep it that way.
- The OAuth session token in OpenAlgo's DB is as sensitive as a password:
  it grants full account access from the whitelisted IP.
