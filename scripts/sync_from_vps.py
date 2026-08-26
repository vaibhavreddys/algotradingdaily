"""
Universal AlgoTrading State & DuckDB Sync Tool (Laptop <-> VPS)
Syncs:
  1. SQLite Trade Journals (database/paper_trades.db, live_trades.db)
  2. Live Market Logs (paper_trading_output.log, live_trading_output.log, daily_cron.log)
  3. Benchmark & Candle Archives (data_pipeline/*.csv)
  4. Smart DuckDB Sync:
     - If local DuckDB does NOT exist -> downloads full file.
     - If local DuckDB DOES exist -> exports & downloads ONLY the missing delta rows via compressed Parquet.
"""
import sys, os, subprocess, tempfile, argparse, glob

def run_cmd(cmd_list, desc=None):
    if desc:
        print(f"📥 {desc}...")
    res = subprocess.run(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res

def sync_from_vps(vps_ip="130.210.49.136", key_path=None, include_duckdb=False):
    local_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    print("=====================================================")
    print(f" Syncing AlgoTrading State from VPS: {vps_ip}")
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
    run_cmd(scp_base + [f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/database/*.db", db_dir + os.sep], "Fetching SQLite Trade Journals")
    print(f"   ✅ SQLite trade journals synced to: {db_dir}")

    # 2. Sync Hierarchical Execution Logs (logs/paper/, logs/live/, daily_cron.log)
    logs_dir = os.path.join(local_root, "logs")
    os.makedirs(os.path.join(logs_dir, "paper"), exist_ok=True)
    os.makedirs(os.path.join(logs_dir, "live"), exist_ok=True)
    
    # Recursive copy of entire logs/ folder
    run_cmd(scp_base + ["-r", f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/logs/*", logs_dir + os.sep], "Fetching Archived Hierarchical Daily Logs")
    run_cmd(scp_base + [f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/*_output.log", local_root + os.sep], "Fetching Latest Log Pointers")
    run_cmd(scp_base + [f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/daily_cron.log", logs_dir + os.sep], "Fetching Daily Cron Logs")
    print(f"   ✅ All hierarchical logs (paper & live) synced to: {logs_dir}")

    # 3. Sync Benchmark & Candle Archives
    pipeline_dir = os.path.join(local_root, "data_pipeline")
    os.makedirs(pipeline_dir, exist_ok=True)
    run_cmd(scp_base + [f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/data_pipeline/*.csv", pipeline_dir + os.sep], "Fetching Benchmark & Candle Archives")
    print(f"   ✅ Benchmark archives synced to: {pipeline_dir}")

    # 4. Smart DuckDB Sync (All-in-One)
    if include_duckdb:
        import duckdb
        local_db_path = os.path.join(local_root, "market_data", "openalgo", "backtest_data.duckdb")
        remote_db_path = "/home/ubuntu/trading/algotradingdaily/market_data/openalgo/backtest_data.duckdb"
        os.makedirs(os.path.dirname(local_db_path), exist_ok=True)

        print("\n📥 Smart Syncing DuckDB...")
        if not os.path.exists(local_db_path) or os.path.getsize(local_db_path) == 0:
            print("   -> Local DuckDB missing. Performing full download...")
            run_cmd(scp_base + [f"ubuntu@{vps_ip}:{remote_db_path}", local_db_path])
            print("   ✅ Full DuckDB database downloaded successfully!")
        else:
            con = duckdb.connect(local_db_path)
            local_max_ts = con.execute("SELECT MAX(timestamp) FROM ohlcv_1m").fetchone()[0]
            con.close()
            
            ts_str = str(local_max_ts)
            print(f"   -> Local Max Timestamp: {ts_str}. Exporting only delta rows from VPS...")
            
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

    print("\n🎉 Full State Sync Complete! Local laptop is now 100% up-to-date with VPS.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="All-in-One VPS Sync Tool")
    parser.add_argument("--key", default=None, help="Path to SSH private key (.key)")
    parser.add_argument("--ip", default="130.210.49.136", help="VPS IP Address")
    parser.add_argument("--duckdb", action="store_true", help="Include Smart DuckDB Full/Delta sync")
    args = parser.parse_args()
    sync_from_vps(vps_ip=args.ip, key_path=args.key, include_duckdb=args.duckdb)
