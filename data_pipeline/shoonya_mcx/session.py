"""Session-token sourcing for the post-OAuth Shoonya API.

Shoonya retired vendor QuickAuth in the 2026 OAuth migration (the endpoint
rejects every vendor-code shape with "Invalid Vendor code"), so this
pipeline cannot mint its own session. Instead it reuses the susertoken that
Shoonya's OAuth flow stores in the co-hosted OpenAlgo instance:

1. ``$SHOONYA_SUSERTOKEN`` if set (manual override), else
2. the newest non-revoked ``shoonya`` row of OpenAlgo's ``auth`` table,
   decrypted with OpenAlgo's own PBKDF2/Fernet scheme.

When the token expires (daily), re-login via the OpenAlgo web UI (or any
OAuth client) and rerun -- no code changes needed.
"""

import base64
import hashlib
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from dotenv import dotenv_values


class SessionTokenError(RuntimeError):
    """No usable Shoonya session token could be sourced."""


def _openalgo_fernet_key(pepper: str, salt_raw: str) -> Fernet:
    """Replicate OpenAlgo's database/auth_db.py::get_encryption_key().

    PBKDF2-HMAC-SHA256 (100k iterations) over the pepper, salted with the
    per-install FERNET_SALT hex (legacy installs fall back to a static salt).
    """
    if salt_raw and len(salt_raw) >= 32:
        try:
            salt = bytes.fromhex(salt_raw)
        except ValueError:
            salt = b"openalgo_static_salt"
    else:
        salt = b"openalgo_static_salt"
    key = base64.urlsafe_b64encode(
        hashlib.pbkdf2_hmac("sha256", pepper.encode(), salt, 100_000)
    )
    return Fernet(key)


def _decrypt_value(cipher: Fernet, stored: str) -> str:
    try:
        return cipher.decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError):
        # OpenAlgo's decrypt_token falls back to the raw value for plaintext
        # rows written before Fernet encryption was introduced.
        return stored


def load_openalgo_session_token(openalgo_dir: Path) -> str:
    """Decrypt the newest Shoonya susertoken from an OpenAlgo checkout's DB."""
    env_path = openalgo_dir / ".env"
    if not env_path.is_file():
        raise SessionTokenError(f"OpenAlgo .env not found at {env_path}")
    env = dotenv_values(env_path)
    pepper = (env.get("API_KEY_PEPPER") or "").strip()
    if not pepper:
        raise SessionTokenError(f"API_KEY_PEPPER missing from {env_path}")

    db_path = openalgo_dir / "db" / "openalgo.db"
    if not db_path.is_file():
        raise SessionTokenError(f"OpenAlgo database not found at {db_path}")

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT auth FROM auth WHERE broker = 'shoonya' AND is_revoked = 0 "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        raise SessionTokenError(
            "No active shoonya auth row in OpenAlgo DB; log in once via the "
            "OpenAlgo web UI so a session token exists."
        )
    return _decrypt_value(_openalgo_fernet_key(pepper, (env.get("FERNET_SALT") or "").strip()), row[0])


def load_session_token(
    susertoken: str = "", openalgo_dir: str = ""
) -> tuple[str, str]:
    """Return (susertoken, source). Raises SessionTokenError when unusable."""
    if susertoken:
        return susertoken, "SHOONYA_SUSERTOKEN"
    if openalgo_dir:
        token = load_openalgo_session_token(Path(openalgo_dir))
        return token, f"OpenAlgo DB ({openalgo_dir})"
    raise SessionTokenError(
        "No session token available. Set SHOONYA_SUSERTOKEN, or OPENALGO_DIR to the "
        "OpenAlgo checkout whose database holds the OAuth session token."
    )
