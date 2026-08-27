"""
yfinance vs OpenAlgo DuckDB Consistency Audit.

Answers: "If I trust the local DuckDB store instead of yfinance, do I get the same answers?"

Phase 1 (DATA)     : Per-symbol 15m OHLCV diff between a fresh yfinance download and the
                     local DuckDB store over the last N calendar days (default 56).
Phase 2 (SIMULATION): Runs the production portfolio simulation twice on identical config,
                     universe and window - once fed by yfinance, once fed by DuckDB -
                     and reports signal-level and capital-level consistency.
                     Both runs share the ^NSEI benchmark so differences isolate candle
                     data quality, not benchmark choice. The proxy-benchmark divergence
                     (what local-only setups must rely on) is reported separately.

Usage:
  python scripts/compare_yf_vs_duckdb.py                 # full audit, last 56 days
  python scripts/compare_yf_vs_duckdb.py --days 30       # shorter window
  python scripts/compare_yf_vs_duckdb.py --limit 5       # smoke test on few symbols
"""

import argparse
import datetime as dt
import os
import sys
from typing import Dict, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import numpy as np
import pandas as pd
import yfinance as yf

from config import CONFIG
from data_pipeline import get_nifty50_symbols
from data_pipeline.openalgo_ingestion import BacktestDataReader
import backtesting.portfolio_sim as psim

TABLE = "ohlcv_15m"
TOL_PCT = 0.05
REPORT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "market_data", "openalgo", "comparison_reports"))
IST = "Asia/Kolkata"


def _ist_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)
    df.index = idx.astype(f"datetime64[ns, {IST}]")
    return df


def _truncate_window(df: pd.DataFrame, start: dt.date, end: dt.date) -> pd.DataFrame:
    dates = pd.DatetimeIndex(df.index).tz_convert(IST).date if pd.DatetimeIndex(df.index).tz is not None else pd.DatetimeIndex(df.index).date
    return df.loc[(dates >= start) & (dates <= end)]


def fetch_yf_frame(ticker: str) -> Optional[pd.DataFrame]:
    for attempt in range(2):
        try:
            raw = yf.download(ticker, period="60d", interval="15m", progress=False)
            if raw is not None and not raw.empty and len(raw) > 0:
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.get_level_values(0)
                raw = raw[["Open", "High", "Low", "Close", "Volume"]]
                return _ist_index(raw.dropna(how="all"))
        except Exception:
            pass
        if attempt == 0:
            import time
            time.sleep(1.5)
    return None


class YFinanceSource:
    def __init__(self, tickers, start: dt.date, end: dt.date):
        self.frames: Dict[str, Optional[pd.DataFrame]] = {}
        for t in tickers:
            df = fetch_yf_frame(t)
            self.frames[t] = _truncate_window(_ist_index(df), start, end) if df is not None else None

    def loader(self, ticker, period=None, interval="15m", force_refresh=False, verbose=False):
        return self.frames.get(ticker)


class DuckDBSource:
    def __init__(self, reader: BacktestDataReader, tickers, start: dt.date, end: dt.date):
        self.reader = reader
        self.frames: Dict[str, Optional[pd.DataFrame]] = {}
        for t in tickers:
            sym = t.replace(".NS", "")
            df = reader.get_full_dataframe(symbol=sym, start_date=start.isoformat(),
                                           end_date=end.isoformat(), table=TABLE)
            if df.empty:
                self.frames[t] = None
                continue
            df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                    "close": "Close", "volume": "Volume"})
            self.frames[t] = _ist_index(df[["Open", "High", "Low", "Close", "Volume"]])

    def loader(self, ticker, period=None, interval="15m", force_refresh=False, verbose=False):
        return self.frames.get(ticker)


def build_nifty_benchmark(start: dt.date, end: dt.date) -> Optional[pd.Series]:
    raw = fetch_yf_frame("^NSEI")
    if raw is None or raw.empty:
        return None
    raw = _truncate_window(raw, start, end)
    daily_opens = raw.groupby(pd.DatetimeIndex(raw.index).date)["Open"].transform("first")
    pct = (raw["Close"] - daily_opens) / daily_opens
    pct.name = "Nifty_Pct"
    return pct


def build_proxy_benchmark(matrix: pd.DataFrame, tickers) -> Optional[pd.Series]:
    cols = [t.replace(".NS", "") for t in tickers]
    cols = [c for c in cols if c in matrix.columns]
    if not cols:
        return None
    m = matrix[cols]
    day_open = m.groupby(m.index.normalize()).transform("first")
    proxy = m.div(day_open).sub(1.0).mean(axis=1)
    proxy.name = "Nifty_Pct"
    return proxy


def data_consistency_report(yf_src: YFinanceSource, db_src: DuckDBSource, tickers) -> pd.DataFrame:
    rows = []
    for t in tickers:
        yf_df, db_df = yf_src.frames.get(t), db_src.frames.get(t)
        row = {"Symbol": t, "YF Bars": 0, "DB Bars": 0, "Matched": 0, "Coverage %": np.nan,
               "Mean |dClose| %": np.nan, "P95 |dClose| %": np.nan, "Max |dClose| %": np.nan,
               ">Tol %": np.nan, "OHLC Match %": np.nan, "Vol Ratio Med": np.nan}
        if yf_df is None or len(yf_df) == 0:
            row["YF Bars"] = 0
            rows.append(row)
            continue
        row["YF Bars"] = len(yf_df)
        if db_df is None or len(db_df) == 0:
            rows.append(row)
            continue
        row["DB Bars"] = len(db_df)

        common = yf_df.index.intersection(db_df.index)
        row["Matched"] = len(common)
        row["Coverage %"] = round(len(common) / len(yf_df) * 100, 1) if len(yf_df) else np.nan
        if len(common) == 0:
            rows.append(row)
            continue

        o, d = yf_df.loc[common], db_df.loc[common]
        d_close_pct = ((o["Close"] - d["Close"]).abs() / d["Close"]) * 100
        row["Mean |dClose| %"] = round(float(d_close_pct.mean()), 4)
        row["P95 |dClose| %"] = round(float(np.percentile(d_close_pct, 95)), 4)
        row["Max |dClose| %"] = round(float(d_close_pct.max()), 4)
        row[">Tol %"] = round(float((d_close_pct > TOL_PCT).mean()) * 100, 2)

        ohlc_ok = pd.concat([
            ((o[c] - d[c]).abs() / d[c].abs().clip(lower=1e-9) * 100) <= TOL_PCT
            for c in ["Open", "High", "Low", "Close"]
        ], axis=1).all(axis=1)
        row["OHLC Match %"] = round(float(ohlc_ok.mean()) * 100, 2)

        vol_mask = o["Volume"] > 0
        if vol_mask.any():
            ratio = (d.loc[vol_mask, "Volume"] / o.loc[vol_mask, "Volume"]).replace([np.inf, -np.inf], np.nan).dropna()
            row["Vol Ratio Med"] = round(float(ratio.median()), 3) if len(ratio) else np.nan
        rows.append(row)

    return pd.DataFrame(rows)


def run_scan(source_loader, symbols, benchmark) -> pd.DataFrame:
    original = psim.load_candle_data
    psim.load_candle_data = source_loader
    try:
        return psim.scan_universe_signals(symbols, benchmark, config=CONFIG, refresh=False)
    finally:
        psim.load_candle_data = original


def run_portfolio(signals_df: pd.DataFrame):
    return psim.simulate_portfolio_execution(signals_df=signals_df, config=CONFIG)


def compare_signal_sets(sig_a: pd.DataFrame, sig_b: pd.DataFrame, la: str, lb: str):
    print(f"\n--- Signal consistency: {la} vs {lb} ---")
    print(f"raw candidate signals : {len(sig_a)} vs {len(sig_b)}")
    if sig_a.empty or sig_b.empty:
        print("⚠️ one side produced no signals; skipping deep compare")
        return None

    ka = sig_a.copy(); kb = sig_b.copy()
    ka['key'] = ka['Symbol'] + '|' + ka['Entry Time'].astype(str)
    kb['key'] = kb['Symbol'] + '|' + kb['Entry Time'].astype(str)

    matched = ka.merge(kb, on='key', suffixes=('_a', '_b'))
    only_a = ka[~ka['key'].isin(kb['key'])]
    only_b = kb[~kb['key'].isin(ka['key'])]

    print(f"matched entries       : {len(matched)}  ({len(matched)/len(ka)*100:.1f}% of {la}, "
          f"{len(matched)/len(kb)*100:.1f}% of {lb})")
    if not matched.empty:
        agree = (matched['Result_a'].astype(str) == matched['Result_b'].astype(str)).mean() * 100
        pnl_delta = (matched['PnL %_a'] - matched['PnL %_b']) * 100
        print(f"exit reason agreement : {agree:.1f}%")
        print(f"|Δ PnL%| per matched trade : mean {pnl_delta.abs().mean()*100:.1f} bps | "
              f"max {pnl_delta.abs().max()*100:.1f} bps")

    if not only_a.empty:
        print(f"{lb}-only misses (top symbols): {only_a['Symbol'].value_counts().head(5).to_dict()}")
    if not only_b.empty:
        print(f"{la}-only extras (top symbols): {only_b['Symbol'].value_counts().head(5).to_dict()}")

    return matched, only_a, only_b


def main() -> int:
    parser = argparse.ArgumentParser(description="yfinance vs DuckDB consistency audit")
    parser.add_argument("--days", type=int, default=56, help="lookback window in calendar days")
    parser.add_argument("--limit", type=int, default=None, help="cap number of symbols (smoke test)")
    args = parser.parse_args()

    today = dt.date.today()
    start, end = today - dt.timedelta(days=args.days), today
    print(f"\n=== WINDOW: {start.isoformat()} -> {end.isoformat()} ({args.days}d, {TABLE}) ===\n")

    nifty_list = get_nifty50_symbols()
    reader = BacktestDataReader()

    matrix = reader.get_close_matrix_sql(start_date=start.isoformat(), end_date=end.isoformat(), table=TABLE)
    if matrix.empty:
        print("❌ DuckDB store has no data in this window.")
        return 1
    db_syms = {str(c).upper() for c in matrix.columns}
    universe = [t for t in nifty_list if t.replace(".NS", "") in db_syms]
    if args.limit:
        universe = universe[:args.limit]
    print(f"universe: {len(universe)} NIFTY50 constituents with local DuckDB data\n")
    if len(universe) < 3:
        print("❌ Too few overlapping symbols.")
        return 1

    print("[1/4] Downloading fresh yfinance 15m candles...")
    yf_src = YFinanceSource(universe, start, end)

    print("[2/4] Loading DuckDB 15m candles...")
    db_src = DuckDBSource(reader, universe, start, end)

    print("[3/4] Computing data-level consistency...")
    report = data_consistency_report(yf_src, db_src, universe)
    valid = report[(report["YF Bars"] > 0) & (report["DB Bars"] > 0)]
    print("\n=== DATA CONSISTENCY (per-symbol summary) ===")
    print(f"symbols compared         : {len(valid)}/{len(report)}")
    print(f"avg coverage of YF bars  : {valid['Coverage %'].mean():.2f}%")
    print(f"mean |Δclose|            : {valid['Mean |dClose| %'].mean():.4f}%")
    print(f"worst mean |Δclose|      : {valid['Mean |dClose| %'].max():.4f}% ({valid.loc[valid['Mean |dClose| %'].idxmax(), 'Symbol']})")
    print(f"bars >{TOL_PCT}% apart     : {valid['>Tol %'].mean():.2f}% avg")
    print(f"OHLC exact-match rate    : {valid['OHLC Match %'].mean():.2f}% avg")
    vr = valid["Vol Ratio Med"].dropna()
    if not vr.empty:
        print(f"volume ratio median (db/yf): {vr.median():.3f}")
    print("\nworst 10 symbols by mean |Δclose|:")
    cols = ["Symbol", "YF Bars", "DB Bars", "Coverage %", "Mean |dClose| %", ">Tol %", "OHLC Match %"]
    print(valid.sort_values("Mean |dClose| %", ascending=False).head(10)[cols].to_string(index=False))

    sim_universe = [t for t in universe
                    if yf_src.frames.get(t) is not None and len(yf_src.frames[t]) >= 50
                    and db_src.frames.get(t) is not None and len(db_src.frames[t]) >= 50]
    print(f"\n[4/4] Running simulations on {len(sim_universe)} fully-covered symbols (shared ^NSEI benchmark)...")

    nifty_pct = build_nifty_benchmark(start, end)
    proxy_pct = build_proxy_benchmark(matrix, universe)
    if nifty_pct is None or nifty_pct.empty:
        print("❌ Could not build ^NSEI benchmark.")
        return 1

    common_idx = proxy_pct.index.intersection(nifty_pct.index) if proxy_pct is not None else None
    if common_idx is not None and len(common_idx):
        bdiff = (proxy_pct.loc[common_idx] - nifty_pct.loc[common_idx]).abs() * 100
        print(f"[benchmark] proxy vs ^NSEI: mean |Δ| {bdiff.mean():.3f} pp | p95 {np.percentile(bdiff, 95):.3f} pp "
              f"(relevant only for local-only setups)")

    sig_yf = run_scan(yf_src.loader, sim_universe, nifty_pct)
    sig_db = run_scan(db_src.loader, sim_universe, nifty_pct)

    cmp_result = compare_signal_sets(sig_yf, sig_db, "YF", "DB")

    tdf_yf, cap_yf, chg_yf, _, _ = run_portfolio(sig_yf)
    tdf_db, cap_db, chg_db, _, _ = run_portfolio(sig_db)
    init = CONFIG.INITIAL_CAPITAL
    ret_yf, ret_db = (cap_yf - init) / init * 100, (cap_db - init) / init * 100

    print("\n=== PORTFOLIO SIMULATION CONSISTENCY ===")
    print(f"executed trades : {len(tdf_yf)} vs {len(tdf_db)}")
    print(f"ending capital  : ₹{cap_yf:,.0f} ({ret_yf:+.2f}%) vs ₹{cap_db:,.0f} ({ret_db:+.2f}%)")
    print(f"difference      : ₹{cap_db - cap_yf:+,.0f}  |  Δreturn {ret_db - ret_yf:+.2f} pp")

    os.makedirs(REPORT_DIR, exist_ok=True)
    stamp = today.strftime("%Y%m%d")
    report_path = os.path.join(REPORT_DIR, f"symbol_consistency_{stamp}.csv")
    report.to_csv(report_path, index=False)
    print(f"\n📄 per-symbol data report -> {report_path}")

    if cmp_result:
        matched, only_a, only_b = cmp_result
        diffs_path = os.path.join(REPORT_DIR, f"signal_diffs_{stamp}.csv")
        pd.concat([
            matched.assign(side="both"),
            only_a.assign(Symbol=lambda d: d["Symbol"], side=f"only_{('YF')}"),
            only_b.assign(side="only_DB"),
        ], ignore_index=True)[["key", "side"] + [c for c in ["Result_a", "Result_b", "PnL %_a", "PnL %_b"] if c in pd.concat([matched.assign(side='both'), only_a.assign(side='x'), only_b.assign(side='x')], ignore_index=True).columns]].to_csv(diffs_path, index=False)
        print(f"📄 signal diff dump      -> {diffs_path}")

    print("\n✅ Audit complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
