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
# Post-OAuth (2026) Shoonya retired the vendor QuickAuth endpoint: the legacy
# /NorenWClientTP/ base answers 502 and /NorenWClientAPI/QuickAuth rejects
# every vendor-code shape ("Invalid Vendor code"). Sessions are therefore
# minted by Shoonya's browser OAuth flow -- in practice by logging into the
# co-hosted OpenAlgo instance -- and this pipeline *reuses* that session
# token instead of logging in itself.
SHOONYA_USER_ID = os.getenv("SHOONYA_USER_ID", "").strip()
# Guard against the BROKER_API_KEY shape ("uid:::client_id") pasted whole:
# the API rejects non-plain uids with "Invalid User Id".
if ":::" in SHOONYA_USER_ID:
    SHOONYA_USER_ID = SHOONYA_USER_ID.split(":::", 1)[0].strip()
SHOONYA_HOST = os.getenv("SHOONYA_HOST", "https://api.shoonya.com/NorenWClientAPI/")
SHOONYA_WS_URL = os.getenv("SHOONYA_WS_URL", "wss://api.shoonya.com/NorenWSTP/")

# Manual override: a live susertoken (64-hex). Takes precedence over the
# OpenAlgo database lookup.
SHOONYA_SUSERTOKEN = os.getenv("SHOONYA_SUSERTOKEN", "")

# Checkout of the OpenAlgo instance whose DB holds the OAuth susertoken.
# Empty disables the DB lookup (then only SHOONYA_SUSERTOKEN can provide it).
OPENALGO_DIR = os.getenv("OPENALGO_DIR", "")

# Legacy vendor credentials -- no longer used for login, kept so old .env
# files do not break validation.
SHOONYA_PASSWORD = os.getenv("SHOONYA_PASSWORD", "")
SHOONYA_TOTP_KEY = os.getenv("SHOONYA_TOTP_KEY", "")
SHOONYA_VENDOR_CODE = os.getenv("SHOONYA_VENDOR_CODE", "")
SHOONYA_API_SECRET = os.getenv("SHOONYA_API_SECRET", "")
SHOONYA_IMEI = os.getenv("SHOONYA_IMEI", "web")

# -------------------------------------------------------------------------
# Instrument directory (scrip master)
# -------------------------------------------------------------------------
# MCX tokens are resolved from Shoonya's official scrip-master file rather
# than the SearchScrip endpoint, which answers "Exchange Not enabled" for
# MCX on retail OAuth apps even though TPSeries/GetQuotes serve MCX fine.
SCRIP_MASTER_URL = os.getenv(
    "SHOONYA_MCX_SCRIP_MASTER_URL", "https://api.shoonya.com/MCX_symbols.txt.zip"
)
SCRIP_MASTER_PATH = STORAGE_DIR / "MCX_symbols.txt"
SCRIP_MASTER_MAX_AGE_HOURS = float(os.getenv("SHOONYA_MCX_SCRIP_MASTER_TTL_HOURS", "24"))

# -------------------------------------------------------------------------
# Ingestion Constants
# -------------------------------------------------------------------------
# '1' = 1-minute candles, the smallest resolution the TPSeries endpoint serves.
INTERVAL = os.getenv("SHOONYA_MCX_INTERVAL", "1")

# Forward chunk width in days. The new gateway kills long TPSeries ranges
# with 504 Server Timeout instead of silently truncating (the legacy
# behaviour the ~1000-candle cap described); 3 days of 1-minute MCX candles
# (~2600 rows) is comfortably inside the observed ~5-day limit.
CHUNK_SIZE_DAYS = int(os.getenv("SHOONYA_MCX_CHUNK_SIZE_DAYS", "3"))
# Safety net: a chunk whose response still carries at least this many candles
# is bisected further (per-request truncation guard).
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
    if not SHOONYA_USER_ID:
        raise RuntimeError("Missing Shoonya credentials: SHOONYA_USER_ID. Set it in .env.")
    if not SHOONYA_SUSERTOKEN and not OPENALGO_DIR:
        raise RuntimeError(
            "No session-token source: set SHOONYA_SUSERTOKEN or OPENALGO_DIR "
            "(pointing at the OpenAlgo checkout whose DB holds the OAuth session)."
        )
    if DELAY_SECONDS < 0 or PROBE_DELAY_SECONDS < 0:
        raise RuntimeError("Delay settings cannot be negative.")
    if MAX_RETRIES < 1:
        raise RuntimeError("SHOONYA_MCX_MAX_RETRIES must be at least 1.")
    if CHUNK_SIZE_DAYS < 1:
        raise RuntimeError("SHOONYA_MCX_CHUNK_SIZE_DAYS must be at least 1.")
    if MAX_CANDLES_PER_REQUEST < 10:
        raise RuntimeError("SHOONYA_MCX_MAX_CANDLES is implausibly small.")
