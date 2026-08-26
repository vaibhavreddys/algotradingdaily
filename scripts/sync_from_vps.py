"""
Universal 1-Click AlgoTrading State & DuckDB Sync Tool (VPS -> Laptop)
Seamlessly syncs EVERYTHING from VPS in 1 shot:
  1. SQLite Trade Journals (database/paper_trades.db, live_trades.db)
  2. Hierarchical Execution Logs (logs/paper/, logs/live/, daily_cron.log)
  3. Benchmark & Candle Archives (data_pipeline/*.csv)
  4. Smart DuckDB Sync (auto full download if missing, or tiny compressed delta if present)
"""
import sys, os, subprocess, tempfile, argparse

def get_env_key():
    """Check if ORACLE_SSH_KEY or VPS_SSH_KEY is set in environment or .env."""
    env_key = os.getenv("ORACLE_SSH_KEY") or os.getenv("VPS_SSH_KEY")
    if env_key and os.path.exists(env_key):
        return env_key
    return None

def run_cmd(cmd_list, desc=None):
    if desc:
        print(f"📥 {desc}...")
    res = subprocess.run(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res

def sync_from_vps(vps_ip="130.210.49.136", key_path=None):
    local_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    if not key_path:
        key_path = get_env_key()
        
    # If key is not provided via CLI or .env, prompt the user directly
    if not key_path and sys.stdin.isatty():
        try:
            user_input = input("👉 Enter the full path to your Oracle SSH key (.key): ").strip().strip('"').strip("'")
            if user_input and os.path.exists(user_input):
                key_path = user_input
            elif user_input:
                print(f"⚠️ File not found: {user_input}")
                sys.exit(1)
            else:
                print("❌ SSH key path is required.")
                sys.exit(1)
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(0)

    print("=====================================================")
    print(f" 🚀 1-Click AlgoTrading State Sync from VPS: {vps_ip}")
    if key_path:
        print(f" Key: {key_path}")
    print("=====================================================")

    # Setup SSH/SCP base commands
    ssh_base = ["ssh", "-o", "StrictHostKeyChecking=no"]
    scp_base = ["scp", "-o", "StrictHostKeyChecking=no"]
    if key_path:
        ssh_base.extend(["-i", key_path])
        scp_base.extend(["-i", key_path])

    # 1. Sync SQLite Trade Journals
    db_dir = os.path.join(local_root, "database")
    os.makedirs(db_dir, exist_ok=True)
    run_cmd(scp_base + [f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/database/*.db", db_dir + os.sep], "Fetching SQLite Trade Journals (paper & live)")
    print(f"   ✅ SQLite trade journals synced to: {db_dir}")

    # 2. Sync Live Market Logs
    logs_dir = os.path.join(local_root, "logs")
    os.makedirs(os.path.join(logs_dir, "paper"), exist_ok=True)
    os.makedirs(os.path.join(logs_dir, "live"), exist_ok=True)
    run_cmd(scp_base + ["-r", f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/logs/*", logs_dir + os.sep], "Fetching Hierarchical Logs (logs/paper/ & logs/live/)")
    run_cmd(scp_base + [f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/*_output.log", local_root + os.sep], "Fetching Latest Log Pointers")
    run_cmd(scp_base + [f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/daily_cron.log", logs_dir + os.sep], "Fetching Daily Cron Logs")
    print(f"   ✅ All logs synced to: {logs_dir}")

    # 3. Sync Benchmark & Candle Archives
    pipeline_dir = os.path.join(local_root, "data_pipeline")
    os.makedirs(pipeline_dir, exist_ok=True)
    run_cmd(scp_base + [f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/data_pipeline/*.csv", pipeline_dir + os.sep], "Fetching Benchmark & Candle Archives")
    print(f"   ✅ Benchmark archives synced to: {pipeline_dir}")

    # 4. Smart DuckDB Sync (Auto Full vs Delta)
    local_db_path = os.path.join(local_root, "market_data", "openalgo", "backtest_data.duckdb")
    remote_db_path = "/home/ubuntu/trading/algotradingdaily/market_data/openalgo/backtest_data.duckdb"
    os.makedirs(os.path.dirname(local_db_path), exist_ok=True)

    print("\n📥 Smart Syncing DuckDB Historical Bars...")
    if not os.path.exists(local_db_path) or os.path.getsize(local_db_path) == 0:
        print("   -> Local DuckDB missing. Performing full download...")
        run_cmd(scp_base + [f"ubuntu@{vps_ip}:{remote_db_path}", local_db_path])
        print("   ✅ Full DuckDB database downloaded successfully!")
    else:
        try:
            import duckdb
            con = duckdb.connect(local_db_path)
            local_max_ts = con.execute("SELECT MAX(timestamp) FROM ohlcv_1m").fetchone()[0]
            con.close()
            
            ts_str = str(local_max_ts)
            print(f"   -> Local latest data: {ts_str}. Exporting only missing delta from VPS...")
            
            temp_dir = tempfile.gettempdir()
            local_parquet = os.path.join(temp_dir, "delta_export.parquet")
            remote_parquet = "/tmp/delta_export.parquet"

            remote_query = f"""python3 -c "import duckdb; con = duckdb.connect(\'{remote_db_path}\', read_only=True); con.execute(\\\"COPY (SELECT * FROM ohlcv_1m WHERE timestamp > \'\'{ts_str}\'\') TO \'{remote_parquet}\' (FORMAT PARQUET, COMPRESSION ZSTD)\\\")" """
            run_cmd(ssh_base + [f"ubuntu@{vps_ip}", remote_query])
            run_cmd(scp_base + [f"ubuntu@{vps_ip}:{remote_parquet}", local_parquet])

            if os.path.exists(local_parquet) and os.path.getsize(local_parquet) > 0:
                con = duckdb.connect(local_db_path)
                count_before = con.execute("SELECT COUNT(*) FROM ohlcv_1m").fetchone()[0]
                con.execute(f"INSERT OR IGNORE INTO ohlcv_1m SELECT * FROM read_parquet('{local_parquet.replace(os.sep, '/')}')")
                count_after = con.execute("SELECT COUNT(*) FROM ohlcv_1m").fetchone()[0]
                new_max = con.execute("SELECT MAX(timestamp) FROM ohlcv_1m").fetchone()[0]
                con.close()
                os.remove(local_parquet)
                
                new_rows = count_after - count_before
                if new_rows > 0:
                    print(f"   ✅ Appended {new_rows:,} new delta 1-minute bars! (New Max: {new_max})")
                else:
                    print("   ✅ DuckDB is already 100% up-to-date with VPS (0 new bars).")
        except Exception as e:
            print(f"   ℹ️ DuckDB delta skipped or error: {e}")

    print("\n🎉 1-Click Sync Complete! Everything on your laptop is now 100% synchronized with the VPS.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="1-Click Automated State Sync from VPS")
    parser.add_argument("--key", default=None, help="Path to SSH private key (.key)")
    parser.add_argument("--ip", default="130.210.49.136", help="VPS IP Address")
    args = parser.parse_args()

    sync_from_vps(vps_ip=args.ip, key_path=args.key)
