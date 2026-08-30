"""Configuration for the Shoonya MCX historical-data ingestion module.

Credentials are read from the environment (or the repository ``.env`` file)
and never hard-coded. See ``.env.example`` for the expected variable names.
"""

import datetime as dt
import os
from pathlib import Path

from dotenv import load_dotenv

# -------------------------------------------------------------------------
# Path & Directory Constants
# -------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

STORAGE_DIR = PROJECT_ROOT / "market_data" / "shoonya_mcx"
DB_PATH = STORAGE_DIR / "mcx_historical_data.duckdb"
LOG_PATH = STORAGE_DIR / "ingestion.log"

# -------------------------------------------------------------------------
# Shoonya (Noren) Account Credentials
# -------------------------------------------------------------------------
SHOONYA_USER_ID = os.getenv("SHOONYA_USER_ID", "")
SHOONYA_PASSWORD = os.getenv("SHOONYA_PASSWORD", "")
SHOONYA_TOTP_KEY = os.getenv("SHOONYA_TOTP_KEY", "")          # Base32 seed for 2FA
SHOONYA_VENDOR_CODE = os.getenv("SHOONYA_VENDOR_CODE", "")
SHOONYA_API_SECRET = os.getenv("SHOONYA_API_SECRET", "")
SHOONYA_IMEI = os.getenv("SHOONYA_IMEI", "web")               # Stable device id string

SHOONYA_HOST = os.getenv("SHOONYA_HOST", "https://api.shoonya.com/NorenWClientTP/")
SHOONYA_WS_URL = os.getenv("SHOONYA_WS_URL", "wss://api.shoonya.com/NorenWSTP/")

# -------------------------------------------------------------------------
# Ingestion Constants
# -------------------------------------------------------------------------
# '1' = 1-minute candles, the smallest resolution the TPSeries endpoint serves.
INTERVAL = os.getenv("SHOONYA_MCX_INTERVAL", "1")

# Nominal forward chunk width in days. Shoonya caps each TPSeries response
# (~MAX_CANDLES_PER_REQUEST candles), so any chunk that saturates the cap is
# adaptively bisected until it fits; this value only bounds the request shape.
CHUNK_SIZE_DAYS = int(os.getenv("SHOONYA_MCX_CHUNK_SIZE_DAYS", "30"))
MAX_CANDLES_PER_REQUEST = int(os.getenv("SHOONYA_MCX_MAX_CANDLES", "1000"))

# Politeness controls to stay inside Shoonya's rate limits (~2 req/sec).
DELAY_SECONDS = float(os.getenv("SHOONYA_MCX_DELAY_SECONDS", "1.0"))
PROBE_DELAY_SECONDS = float(os.getenv("SHOONYA_MCX_PROBE_DELAY_SECONDS", "1.0"))
MAX_RETRIES = int(os.getenv("SHOONYA_MCX_MAX_RETRIES", "5"))

# Binary-search window for historical boundary discovery.
SEARCH_START = dt.date.fromisoformat(os.getenv("SHOONYA_MCX_SEARCH_START", "2015-01-01"))
PROBE_WINDOW_DAYS = int(os.getenv("SHOONYA_MCX_PROBE_WINDOW_DAYS", "7"))

# Re-download this many trailing days on incremental runs so candles that
# arrive late (session-close corrections) are refreshed via upsert.
RESUME_OVERLAP_DAYS = int(os.getenv("SHOONYA_MCX_RESUME_OVERLAP_DAYS", "1"))

# All timestamps stored in DuckDB are naive IST (Asia/Kolkata), matching the
# broker's own candle strings; convert at read time if UTC is required.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")


def validate_settings() -> None:
    """Fail fast on missing credentials or unsafe tuning values."""
    missing = [
        name
        for name, value in (
            ("SHOONYA_USER_ID", SHOONYA_USER_ID),
            ("SHOONYA_PASSWORD", SHOONYA_PASSWORD),
            ("SHOONYA_TOTP_KEY", SHOONYA_TOTP_KEY),
            ("SHOONYA_VENDOR_CODE", SHOONYA_VENDOR_CODE),
            ("SHOONYA_API_SECRET", SHOONYA_API_SECRET),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing Shoonya credentials: " + ", ".join(missing)
            + ". Set them in .env or the environment."
        )
    if DELAY_SECONDS < 0 or PROBE_DELAY_SECONDS < 0:
        raise RuntimeError("Delay settings cannot be negative.")
    if MAX_RETRIES < 1:
        raise RuntimeError("SHOONYA_MCX_MAX_RETRIES must be at least 1.")
    if CHUNK_SIZE_DAYS < 1:
        raise RuntimeError("SHOONYA_MCX_CHUNK_SIZE_DAYS must be at least 1.")
    if MAX_CANDLES_PER_REQUEST < 10:
        raise RuntimeError("SHOONYA_MCX_MAX_CANDLES is implausibly small.")
