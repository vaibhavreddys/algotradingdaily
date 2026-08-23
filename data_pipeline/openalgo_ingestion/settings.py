"""Configuration for the isolated OpenAlgo historical-data store."""

import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    if value.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if value.strip().lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


# -------------------------------------------------------------------------
# Path & Directory Constants
# -------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORAGE_DIR = PROJECT_ROOT / "market_data" / "openalgo"
DB_PATH = STORAGE_DIR / "backtest_data.duckdb"
STATE_DB_PATH = STORAGE_DIR / "download_state.sqlite"
LOG_PATH = STORAGE_DIR / "ingestion.log"
ARCHIVE_DIR = STORAGE_DIR / "archive" / "ohlcv_1m"

# -------------------------------------------------------------------------
# OpenAlgo Connection & Ingestion Constants
# -------------------------------------------------------------------------
OPENALGO_HOST = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000").rstrip("/")
OPENALGO_API_KEY = os.getenv("OPENALGO_API_KEY", "")
EXCHANGE = os.getenv("OPENALGO_EXCHANGE", "NSE").upper()
INTERVAL = os.getenv("OPENALGO_INTERVAL", "1m")
SHOONYA_APPEND_EQ = _env_bool("OPENALGO_SHOONYA_APPEND_EQ", False)
DELAY_SECONDS = float(os.getenv("OPENALGO_DELAY_SECONDS", "5"))
MAX_RETRIES = int(os.getenv("OPENALGO_MAX_RETRIES", "5"))
CHUNK_SIZE_DAYS = int(os.getenv("OPENALGO_CHUNK_SIZE_DAYS", "30"))
PROBE_DELAY_SECONDS = float(os.getenv("OPENALGO_PROBE_DELAY_SECONDS", "1"))


def validate_settings() -> None:
    """Validate values that can otherwise cause unsafe or surprising requests."""
    if not OPENALGO_API_KEY:
        raise RuntimeError("OPENALGO_API_KEY is not configured. Set it in .env or the environment.")
    if not OPENALGO_HOST:
        raise RuntimeError("OPENALGO_HOST is not configured.")
    if DELAY_SECONDS < 0:
        raise RuntimeError("OPENALGO_DELAY_SECONDS cannot be negative.")
    if PROBE_DELAY_SECONDS < 0:
        raise RuntimeError("OPENALGO_PROBE_DELAY_SECONDS cannot be negative.")
    if MAX_RETRIES < 1:
        raise RuntimeError("OPENALGO_MAX_RETRIES must be at least 1.")
    if not 1 <= CHUNK_SIZE_DAYS <= 30:
        raise RuntimeError("OPENALGO_CHUNK_SIZE_DAYS must be between 1 and 30.")
