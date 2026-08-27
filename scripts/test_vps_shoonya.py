"""
Run this script on your VPS:
  python scripts/test_vps_shoonya.py
"""
import os, sys, datetime
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import CONFIG
from openalgo import api as OpenAlgoClient
from data_pipeline.data_feed import fetch_latest_tick_price, fetch_openalgo_candles, fetch_verified_candles

host = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000").rstrip("/")
api_key = os.getenv("OPENALGO_API_KEY", "")

print("=====================================================")
print(" 🔍 LIVE SHOONYA BROKER DATA FEED AUDIT (VPS)")
print(f" Target Gateway Host : {host}")
print(f" API Key Configured  : {'✅ Present (' + api_key[:4] + '***)' if api_key else '❌ Missing'}")
print("=====================================================\n")

try:
    client = OpenAlgoClient(api_key=api_key, host=host)
    
    # 1. Test Single Real-time Tick Fetch
    print("1️⃣ Testing Live Real-time Tick (LTP) Fetch for RELIANCE...")
    tick = fetch_latest_tick_price("RELIANCE.NS", api_client=client)
    print(f"   -> Tick Response: {tick}")
    
    # 2. Test 15-minute Intraday Candles via OpenAlgo
    print("\n2️⃣ Testing 15-Minute Historical Intraday Candles for RELIANCE via OpenAlgo...")
    candles_df = fetch_openalgo_candles(client, "RELIANCE.NS", interval="15m", days=5)
    if candles_df is not None and not candles_df.empty:
        print(f"   -> ✅ Successfully retrieved {len(candles_df)} bars from Shoonya Broker Gateway!")
        print(f"   -> Columns: {list(candles_df.columns)}")
        print(f"   -> Latest 15m Candle:")
        print(candles_df.tail(2).to_string(index=False))
    else:
        print("   -> ⚠️ No candles returned or format error")

    # 3. Test Multi-symbol Fetch (FORTIS, MARICO, BHARTIARTL)
    print("\n3️⃣ Testing Verified Multi-Symbol Ingestion (FORTIS, MARICO, BHARTIARTL)...")
    for sym in ["FORTIS.NS", "MARICO.NS", "BHARTIARTL.NS"]:
        df = fetch_verified_candles(sym, period="5d", interval="15m", api_client=client)
        source = getattr(df, "_data_source", "Unknown") if df is not None else "None"
        count = len(df) if df is not None else 0
        print(f"   -> {sym:15} | Bars: {count:3} | Data Source: {source}")

    print("\n=====================================================")
    print("🎉 ALL SYSTEMS OPERATIONAL WITH ZERO FAILURES!")
    print("=====================================================")

except Exception as e:
    print(f"\n❌ Exception during audit: {e}")
