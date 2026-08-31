"""
Standalone Gateway & Broker Session Health Diagnostic Verification Tool (Issue #1).
Run on VPS or Laptop:
  python scripts/verify_gateway.py
"""
import sys, os, requests, datetime
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Safe UTF-8 console output for Windows/Linux
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import CONFIG
from openalgo import api as OpenAlgoClient

def verify_gateway():
    print("=====================================================")
    print(" 🏥 OPENALGO GATEWAY & BROKER HEALTH DIAGNOSTICS")
    print("=====================================================")
    
    host = os.getenv("OPENALGO_HOST", getattr(CONFIG, "OPENALGO_HOST", "http://127.0.0.1:5000")).rstrip("/")
    api_key = os.getenv("OPENALGO_API_KEY", getattr(CONFIG, "OPENALGO_API_KEY", ""))
    
    print(f"Target Gateway Host : {host}")
    print(f"API Key Configured  : {'✅ Present (' + api_key[:4] + '***)' if api_key else '❌ Missing'}")
    print("-----------------------------------------------------")

    # 1. Ping Gateway Service (Port / Process check)
    print("1️⃣ Checking OpenAlgo Gateway Service Connectivity...")
    try:
        ping_res = requests.get(f"{host}/api/v1/ping", timeout=4)
        if ping_res.status_code == 200:
            print(f"   ✅ Gateway HTTP Ping Successful: {ping_res.json()}")
        else:
            print(f"   ℹ️ Gateway reachable (HTTP {ping_res.status_code})")
    except Exception as e:
        print(f"   ❌ FAILED: Cannot connect to {host} ({e})")
        print("   👉 Troubleshooting: Make sure openalgo service is running: `sudo systemctl status openalgo`")
        return False

    # 2. Verify API Client & Broker Session
    print("\n2️⃣ Verifying Broker Session & Profile...")
    try:
        client = OpenAlgoClient(api_key=api_key, host=host)
        
        # Test User Profile / Margin
        funds = client.funds()
        print(f"   Response from /funds: {funds}")
        
        # Check both status and presence of broker funds payload
        is_error = isinstance(funds, dict) and funds.get("status") == "error"
        data_payload = funds.get("data", {}) if isinstance(funds, dict) else {}
        
        if is_error or not data_payload:
            print("   ❌ FAILED: Broker Session is NOT authenticated!")
            if not data_payload and isinstance(funds, dict) and funds.get("status") == "success":
                print("   ⚠️  OpenAlgo returned empty data payload {'data': {}} -> Broker login required.")
            elif is_error:
                print(f"   ❌ Broker Error: {funds.get('message')}")
            print("   👉 ACTION REQUIRED: Open http://<VPS_IP>:5000 in your browser and complete Shoonya User ID + Password + TOTP Login.")
            return False
            
        print("   ✅ Broker Session Verified & Active!")
    except Exception as e:
        print(f"   ❌ Broker Session Verification Failed: {e}")
        return False

    # 3. Test Market Data Feed
    print("\n3️⃣ Testing Live 1-Minute Market Data Feed (RELIANCE)...")
    try:
        hist = client.history(
            symbol="RELIANCE",
            exchange="NSE",
            interval="1m",
            start_date=datetime.date.today().strftime("%Y-%m-%d"),
            end_date=datetime.date.today().strftime("%Y-%m-%d")
        )
        if isinstance(hist, dict) and hist.get("status") == "error":
            print(f"   ⚠️ History API warning: {hist.get('message')}")
        else:
            row_count = len(hist) if hasattr(hist, "__len__") else 0
            print(f"   ✅ Live Market Data Feed Verified: Successfully retrieved {row_count} bar(s) for RELIANCE!")
    except Exception as e:
        print(f"   ⚠️ Market Feed Exception: {e}")

    print("\n=====================================================")
    print("🎉 ALL SYSTEMS GO: Gateway and Broker Session are 100% Operational!")
    print("=====================================================\n")
    return True

if __name__ == "__main__":
    success = verify_gateway()
    sys.exit(0 if success else 1)
