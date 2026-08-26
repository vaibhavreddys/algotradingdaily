"""
Universal AlgoTrading State & DuckDB Sync Tool (Laptop <-> VPS)
Supports both Interactive Wizard Mode and Direct CLI Flags.
"""
import sys, os, subprocess, tempfile, argparse

def run_cmd(cmd_list, desc=None):
    if desc:
        print(f"📥 {desc}...")
    res = subprocess.run(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res

def prompt_yes_no(question, default=True):
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        val = input(f"👉 {question}{suffix}").strip().lower()
        if not val:
            return default
        return val in ["y", "yes", "true", "1"]
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(0)

def sync_from_vps(vps_ip="130.210.49.136", key_path=None, interactive=False, sync_db=True, sync_logs=True, sync_csv=True, sync_duckdb=False):
    local_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    print("=====================================================")
    print(f"       ALGOTRADING VPS STATE SYNC TOOL")
    print(f"       Target VPS: {vps_ip}")
    print("=====================================================")

    # Setup SSH/SCP base commands
    ssh_base = ["ssh", "-o", "StrictHostKeyChecking=no"]
    scp_base = ["scp", "-o", "StrictHostKeyChecking=no"]
    if key_path:
        ssh_base.extend(["-i", key_path])
        scp_base.extend(["-i", key_path])

    if interactive:
        print("\nSelect which components to sync to your laptop:")
        sync_db = prompt_yes_no("1. Sync SQLite Trade Journals (paper_trades.db, live_trades.db)?", default=True)
        sync_logs = prompt_yes_no("2. Sync Hierarchical Execution Logs (logs/paper/, logs/live/)?", default=True)
        sync_csv = prompt_yes_no("3. Sync Benchmark & Candle CSV Archives (data_pipeline/*.csv)?", default=True)
        sync_duckdb = prompt_yes_no("4. Sync DuckDB Historical Bars (Smart Incremental Delta)?", default=False)
        print("-----------------------------------------------------")

    # 1. Sync SQLite Trade Journals
    if sync_db:
        db_dir = os.path.join(local_root, "database")
        os.makedirs(db_dir, exist_ok=True)
        run_cmd(scp_base + [f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/database/*.db", db_dir + os.sep], "Fetching SQLite Trade Journals")
        print(f"   ✅ SQLite trade journals synced to: {db_dir}")

    # 2. Sync Live Market Logs
    if sync_logs:
        logs_dir = os.path.join(local_root, "logs")
        os.makedirs(os.path.join(logs_dir, "paper"), exist_ok=True)
        os.makedirs(os.path.join(logs_dir, "live"), exist_ok=True)
        run_cmd(scp_base + ["-r", f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/logs/*", logs_dir + os.sep], "Fetching Archived Hierarchical Daily Logs")
        run_cmd(scp_base + [f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/*_output.log", local_root + os.sep], "Fetching Latest Log Pointers")
        run_cmd(scp_base + [f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/daily_cron.log", logs_dir + os.sep], "Fetching Daily Cron Logs")
        print(f"   ✅ All hierarchical logs (paper & live) synced to: {logs_dir}")

    # 3. Sync Benchmark & Candle Archives
    if sync_csv:
        pipeline_dir = os.path.join(local_root, "data_pipeline")
        os.makedirs(pipeline_dir, exist_ok=True)
        run_cmd(scp_base + [f"ubuntu@{vps_ip}:/home/ubuntu/trading/algotradingdaily/data_pipeline/*.csv", pipeline_dir + os.sep], "Fetching Benchmark & Candle Archives")
        print(f"   ✅ Benchmark archives synced to: {pipeline_dir}")

    # 4. Smart DuckDB Sync (Auto Full vs Delta)
    if sync_duckdb:
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

    print("\n🎉 Selected components synced successfully! Local laptop is now up-to-date.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Interactive & Automated VPS Sync Tool")
    parser.add_argument("--key", default=None, help="Path to SSH private key (.key)")
    parser.add_argument("--ip", default="130.210.49.136", help="VPS IP Address")
    parser.add_argument("-i", "--interactive", action="store_true", help="Launch interactive step-by-step Yes/No wizard")
    parser.add_argument("--all", action="store_true", help="Sync all components including DuckDB delta without prompt")
    parser.add_argument("--duckdb", action="store_true", help="Include Smart DuckDB Full/Delta sync")
    args = parser.parse_args()

    # Interactive mode is default when running in a terminal without specific flags
    is_interactive = args.interactive or (not args.all and not args.duckdb and sys.stdin.isatty())
    
    sync_from_vps(
        vps_ip=args.ip,
        key_path=args.key,
        interactive=is_interactive,
        sync_duckdb=args.all or args.duckdb
    )
