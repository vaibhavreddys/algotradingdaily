"""Preflight: verify the session/token plumbing behind the Shoonya MCX pipeline.

Shoonya retired vendor QuickAuth in the 2026 OAuth migration, so this script
no longer performs a login. Instead it checks that a session token can be
sourced (env var or OpenAlgo's DB) and that the broker accepts it.
"""

import sys
import os

# Ensure repository root is on sys.path when executed from scripts/.
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from data_pipeline.shoonya_mcx import settings
from data_pipeline.shoonya_mcx.downloader import (
    MCXIngestionEngine,
    ShoonyaAuthError,
    ShoonyaGatewayError,
    ShoonyaHttpClient,
)
from data_pipeline.shoonya_mcx.session import load_session_token

# 1. Config sanity (user id present, a token source is configured).
try:
    settings.validate_settings()
    print("[OK] Settings valid (SHOONYA_USER_ID + a session-token source present)")
except RuntimeError as exc:
    print(f"[FAIL] {exc}")
    sys.exit(1)

# 2. A session token can be sourced from disk/env.
try:
    token, source = load_session_token(
        susertoken=settings.SHOONYA_SUSERTOKEN,
        openalgo_dir=settings.OPENALGO_DIR,
    )
    print(f"[OK] Session token sourced from {source} (length={len(token)})")
except RuntimeError as exc:
    print(f"[FAIL] {exc}")
    sys.exit(1)

# 3. The broker accepts the token (Limits probe) + one live TPSeries call.
engine = MCXIngestionEngine(
    api=ShoonyaHttpClient(settings.SHOONYA_HOST, settings.SHOONYA_USER_ID, token)
)
try:
    engine.authenticate()
except ShoonyaGatewayError as exc:
    print(f"[GATEWAY] Request reached Shoonya but the gateway is unhealthy (not a token issue): {exc}")
    sys.exit(1)
except ShoonyaAuthError as exc:
    print(f"[FAIL] {exc}")
    sys.exit(1)
print("[OK] Live session VALID -- /Limits accepted the token")
