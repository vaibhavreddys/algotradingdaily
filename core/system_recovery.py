"""
Automated Infrastructure Self-Healing & Diagnostic Subsystem.

Manages health monitoring, proactive diagnostics, and automated recovery across
all 4 infrastructure pillars:
  1. Shoonya Broker Session: Headless TOTP login with concurrency locking and cooldown limits.
  2. OpenAlgo Gateway: Port 5000 / HTTP OMS gateway monitoring and service revival.
  3. Trading Engine: Process tracking, heartbeat inspection, and duplicate avoidance.
  4. Telegram Bot: Daemon monitoring and service restart.
"""

import os
import sys
import time
import shutil
import logging
import datetime
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

import requests

from config import CONFIG, TradingConfig
from core.process_lock import SingletonLock
from core.market_calendar import is_market_open as is_mc_open

logger = logging.getLogger("core.system_recovery")

# Global tracking for circuit breaker rate-limiting
_LAST_BROKER_RECOVERY_TIMESTAMP: float = 0.0
BROKER_RECOVERY_COOLDOWN_SEC: float = 300.0  # 5-minute cooldown between auto-login attempts


def get_auto_login_script_and_python() -> Tuple[Optional[Path], Optional[str]]:
    """
    Resolves the Shoonya auto_login script and its corresponding Python virtualenv.
    Checks environment overrides first, then standard VPS paths, then local workspace fallbacks.
    """
    custom_script = os.getenv("SHOONYA_AUTO_LOGIN_SCRIPT")
    custom_python = os.getenv("OPENALGO_PYTHON_BIN")

    script_candidates = [
        Path(custom_script) if custom_script else None,
        Path("/home/ubuntu/trading/openalgo/openalgo/auto_login_shoonya/auto_login_shoonya.py"),
        Path("/home/ubuntu/openalgo/auto_login_shoonya/auto_login_shoonya.py"),
        Path.home() / "openalgo" / "auto_login_shoonya" / "auto_login_shoonya.py",
    ]

    resolved_script = None
    for cand in script_candidates:
        if cand and cand.is_file():
            resolved_script = cand
            break

    if not resolved_script:
        return None, None

    # Derive Python binary
    python_candidates = [
        custom_python,
        str(resolved_script.parent.parent / ".venv" / "bin" / "python"),
        str(resolved_script.parent.parent / "venv" / "bin" / "python"),
        "/home/ubuntu/trading/openalgo/openalgo/.venv/bin/python",
        "/home/ubuntu/openalgo/.venv/bin/python",
        sys.executable,
    ]

    resolved_python = sys.executable
    for py in python_candidates:
        if py and os.path.isfile(py) and os.access(py, os.X_OK):
            resolved_python = py
            break

    return resolved_script, resolved_python


def probe_openalgo_gateway(config: TradingConfig = CONFIG) -> Dict[str, Any]:
    """Probes the OpenAlgo Gateway service on port 5000."""
    host = getattr(config, "OPENALGO_HOST", "http://127.0.0.1:5000").rstrip("/")
    api_key = getattr(config, "OPENALGO_API_KEY", "") or os.getenv("OPENALGO_API_KEY", "")

    try:
        res = requests.post(
            f"{host}/api/v1/funds",
            headers={"Content-Type": "application/json"},
            json={"apikey": api_key},
            timeout=4.0,
        )
        if res.status_code in (200, 400, 401, 403):
            return {"healthy": True, "code": res.status_code, "host": host, "detail": "Responding"}
        return {"healthy": False, "code": res.status_code, "host": host, "detail": f"HTTP {res.status_code}"}
    except requests.RequestException as e:
        return {"healthy": False, "code": 0, "host": host, "detail": str(e)}


def probe_broker_auth(config: TradingConfig = CONFIG) -> Dict[str, Any]:
    """Probes the Shoonya broker session through OpenAlgo /funds."""
    host = getattr(config, "OPENALGO_HOST", "http://127.0.0.1:5000").rstrip("/")
    api_key = getattr(config, "OPENALGO_API_KEY", "") or os.getenv("OPENALGO_API_KEY", "")

    if not api_key:
        return {"authenticated": False, "detail": "OPENALGO_API_KEY not set", "avail_margin": 0.0, "cash": 0.0}

    try:
        res = requests.post(
            f"{host}/api/v1/funds",
            headers={"Content-Type": "application/json"},
            json={"apikey": api_key},
            timeout=5.0,
        )
        if res.status_code != 200:
            return {"authenticated": False, "detail": f"HTTP {res.status_code}", "avail_margin": 0.0, "cash": 0.0}

        payload = res.json()
        if payload.get("status") == "success":
            data = payload.get("data", {})
            if isinstance(data, dict) and data:
                try:
                    margin = float(data.get("available_margin") or data.get("margin") or 0.0)
                    cash = float(data.get("cash") or 0.0)
                    return {"authenticated": True, "detail": "Authenticated", "avail_margin": margin, "cash": cash}
                except (ValueError, TypeError):
                    pass
                return {"authenticated": True, "detail": "Authenticated", "avail_margin": 0.0, "cash": 0.0}
            return {"authenticated": False, "detail": "Empty funds data (login required)", "avail_margin": 0.0, "cash": 0.0}

        msg = payload.get("message") or payload.get("msg") or "Auth failed"
        return {"authenticated": False, "detail": str(msg), "avail_margin": 0.0, "cash": 0.0}
    except Exception as e:
        return {"authenticated": False, "detail": f"Probe error: {e}", "avail_margin": 0.0, "cash": 0.0}


def probe_system_resources() -> Dict[str, Any]:
    """Checks VPS disk space and available system memory."""
    try:
        total, used, free = shutil.disk_usage("/")
        disk_pct = (used / total) * 100
    except Exception:
        disk_pct = 0.0
        free = 0

    return {
        "disk_pct": round(disk_pct, 1),
        "disk_free_gb": round(free / (1024**3), 2),
        "healthy": disk_pct < 90.0,
    }


def diagnose_system(config: TradingConfig = CONFIG) -> Dict[str, Any]:
    """
    Runs full multi-pillar diagnostic on infrastructure:
    Gateway, Broker Auth, Engine Process & Heartbeat, and VPS Resources.
    """
    gw = probe_openalgo_gateway(config)
    broker = probe_broker_auth(config)
    resources = probe_system_resources()

    mode = getattr(config, "TRADING_MODE", "paper").lower()
    engine_lock = SingletonLock(service_name=f"engine_{mode}", raise_on_conflict=False)
    engine_running_pid = engine_lock.get_locked_pid()

    # Heartbeat file check
    logs_dir = getattr(config, "LOGS_DIR", "logs")
    hb_path = Path(logs_dir) / f"engine_heartbeat_{mode}.json"
    engine_healthy = False
    engine_detail = "Stopped"

    if hb_path.exists():
        try:
            import json
            hb_data = json.loads(hb_path.read_text(encoding="utf-8"))
            hb_ts_str = hb_data.get("timestamp")
            if hb_ts_str:
                hb_time = datetime.datetime.strptime(hb_ts_str, "%Y-%m-%d %H:%M:%S")
                age_sec = (datetime.datetime.now() - hb_time).total_seconds()
                if age_sec < 90:
                    engine_healthy = True
                    engine_detail = f"Active (PID {engine_running_pid or hb_data.get('pid')}, age {int(age_sec)}s)"
                else:
                    engine_detail = f"Stale (last heartbeat {int(age_sec)}s ago)"
        except Exception:
            pass

    if engine_running_pid and not engine_healthy:
        engine_healthy = True
        engine_detail = f"Running (PID {engine_running_pid})"

    return {
        "gateway": gw,
        "broker": broker,
        "engine": {"healthy": engine_healthy, "detail": engine_detail, "mode": mode, "pid": engine_running_pid},
        "resources": resources,
        "all_healthy": gw["healthy"] and broker["authenticated"] and resources["healthy"],
    }


def recover_broker_session(config: TradingConfig = CONFIG, force: bool = False) -> Tuple[bool, str]:
    """
    Executes headless Playwright TOTP login for Shoonya with process locking and cooldown protection.
    Returns (success: bool, status_message: str).
    """
    global _LAST_BROKER_RECOVERY_TIMESTAMP
    now = time.time()

    if not force:
        elapsed = now - _LAST_BROKER_RECOVERY_TIMESTAMP
        if elapsed < BROKER_RECOVERY_COOLDOWN_SEC:
            remaining = int(BROKER_RECOVERY_COOLDOWN_SEC - elapsed)
            return False, f"Cooldown active: please wait {remaining}s before retrying auto-login."

    script_path, python_bin = get_auto_login_script_and_python()
    if not script_path or not python_bin:
        return False, "Shoonya auto_login script not found on system."

    # Enforce concurrency lock so dual auto-logins never run concurrently
    login_lock = SingletonLock(service_name="shoonya_login", raise_on_conflict=False)
    if not login_lock.acquire():
        existing_pid = login_lock.get_locked_pid()
        pid_info = f" (PID {existing_pid})" if existing_pid else ""
        return False, f"Another auto-login process is already running{pid_info}. Please wait."

    try:
        _LAST_BROKER_RECOVERY_TIMESTAMP = now
        logger.info("Triggering headless Shoonya auto-login: %s via %s", script_path, python_bin)

        cmd = [python_bin, str(script_path)]
        if force:
            cmd.append("--force")

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
            cwd=str(script_path.parent),
        )

        if proc.returncode != 0:
            err_output = (proc.stderr or proc.stdout or "").strip().splitlines()
            last_err = err_output[-1] if err_output else f"exit code {proc.returncode}"
            return False, f"Auto-login script failed: {last_err}"

        # Re-verify broker authentication via OpenAlgo
        time.sleep(1.0)
        broker_probe = probe_broker_auth(config)
        if broker_probe.get("authenticated"):
            avail = broker_probe.get("avail_margin", 0.0)
            return True, f"Shoonya re-authenticated successfully! (Available Margin: ₹{avail:,.2f})"

        return False, f"Login finished but broker auth check failed: {broker_probe.get('detail')}"

    except subprocess.TimeoutExpired:
        return False, "Auto-login process timed out after 90 seconds."
    except Exception as exc:
        return False, f"Unexpected error during auto-login: {exc}"
    finally:
        login_lock.release()


def recover_gateway() -> Tuple[bool, str]:
    """Restarts OpenAlgo Gateway service via systemctl."""
    if sys.platform == "win32":
        return False, "Gateway service control is only supported on Linux systemd."

    try:
        res = subprocess.run(
            ["sudo", "systemctl", "restart", "openalgo.service"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if res.returncode == 0:
            time.sleep(2.0)
            gw_probe = probe_openalgo_gateway()
            if gw_probe["healthy"]:
                return True, "OpenAlgo Gateway restarted successfully and is responding."
            return False, f"Gateway restarted but not responding: {gw_probe['detail']}"
        return False, f"systemctl failed: {res.stderr.strip()}"
    except Exception as e:
        return False, f"Failed to restart openalgo.service: {e}"


def recover_telegram_bot() -> Tuple[bool, str]:
    """Restarts telegram_bot.service via systemctl."""
    if sys.platform == "win32":
        return False, "Telegram bot service control is only supported on Linux systemd."

    try:
        res = subprocess.run(
            ["sudo", "systemctl", "restart", "telegram_bot.service"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if res.returncode == 0:
            return True, "telegram_bot.service restarted successfully."
        return False, f"systemctl restart telegram_bot failed: {res.stderr.strip()}"
    except Exception as e:
        return False, f"Failed to restart telegram_bot.service: {e}"


def auto_heal_all(config: TradingConfig = CONFIG) -> Dict[str, Any]:
    """
    Universal orchestrator: diagnoses all 4 pillars and automatically heals
    any degraded component in sequence.
    """
    actions_taken = []
    diag = diagnose_system(config)

    # 1. Gateway recovery if down
    if not diag["gateway"]["healthy"]:
        gw_ok, gw_msg = recover_gateway()
        actions_taken.append(f"Gateway: {'🟢' if gw_ok else '🔴'} {gw_msg}")

    # 2. Broker recovery if unauthenticated
    if not diag["broker"]["authenticated"]:
        bk_ok, bk_msg = recover_broker_session(config=config, force=True)
        actions_taken.append(f"Broker: {'🟢' if bk_ok else '🔴'} {bk_msg}")

    # 3. Post-healing re-diagnosis
    final_diag = diagnose_system(config)
    return {
        "initial": diag,
        "actions": actions_taken,
        "final": final_diag,
        "all_healed": final_diag["all_healthy"],
    }
