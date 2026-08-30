"""
Market Data Ingestion & Gateway Layer.

Unified data gateway for the project:
  - Dynamic NSE NIFTY 50 constituent retrieval with fallback
  - NIFTY 50 benchmark downloader with local archiving & intraday % return
  - Smart stock candle loader with local CSV archiving, freshness checks & force refresh
"""

import io
import os
import sys
import requests
# pyrefly: ignore [missing-import]
import yfinance as yf
import pandas as pd
from typing import List, Optional, Dict, Any, Tuple


# Global benchmark in-memory cache to avoid recomputing 5,391-bar DuckDB multi-CTE aggregations
_BENCHMARK_CACHE: dict = {}

from data_pipeline.openalgo_ingestion import settings
from data_pipeline.openalgo_ingestion.reader import BacktestDataReader

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

CACHE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "market_data"))

DEFAULT_NIFTY50_FALLBACK = [
    "GRASIM.NS", "DIXON.NS", "TATAMOTORS.NS", "INFY.NS", "RELIANCE.NS",
    "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "BHARTIARTL.NS", "ITC.NS",
    "TCS.NS", "LT.NS", "AXISBANK.NS", "KOTAKBANK.NS", "HINDUNILVR.NS"
]


def get_available_symbols(universe: str = "NIFTY50") -> List[str]:
    """
    Returns symbols for backtesting:
      - If DuckDB exists, queries distinct symbols from database.
      - Otherwise falls back to live/cached NIFTY 50 list.
    """
    if os.path.exists(settings.DB_PATH):
        try:
            reader = BacktestDataReader()
            symbols = reader.get_symbols(table="ohlcv_15m")
            if symbols:
                clean_syms = sorted(list(set(symbols)))
                if universe.upper() == "NIFTY50":
                    nifty50_clean = set(s.replace(".NS", "").replace("^NSEI", "") for s in get_nifty50_symbols())
                    filtered = [s for s in clean_syms if s in nifty50_clean]
                    return sorted([f"{s}.NS" for s in filtered]) if filtered else sorted([f"{s}.NS" for s in clean_syms[:50]])
                return [f"{s}.NS" for s in clean_syms]
        except Exception:
            pass
    return sorted(get_nifty50_symbols())


def get_symbols_for_universe(universe: str = "NIFTY50") -> List[str]:
    """
    Returns standardized list of active constituent symbols for the given universe (NIFTY50 or NIFTY200).
    """
    u = (universe or "NIFTY50").upper()
    if u == "NIFTY200":
        return get_nifty200_symbols()
    return get_nifty50_symbols()


def get_nifty200_symbols() -> List[str]:
    """
    Returns standardized list of NIFTY 200 constituent symbols.
    """
    try:
        from data_pipeline.openalgo_ingestion.scraper import NSEConstituentFetcher
        fetcher = NSEConstituentFetcher()
        syms = fetcher.get_index_symbols("NIFTY200")
        if syms and len(syms) >= 100:
            return sorted(list(set(syms)))
    except Exception:
        pass

    try:
        from data_pipeline.openalgo_ingestion.reader import DuckDbReader
        from data_pipeline.openalgo_ingestion.settings import DUCKDB_PATH
        reader = DuckDbReader(DUCKDB_PATH)
        syms = reader.get_available_symbols()
        if syms and len(syms) >= 100:
            return sorted(list(set(syms)))
    except Exception:
        pass
    return get_nifty50_symbols()


def get_nifty50_symbols() -> List[str]:
    """
    Fetches the live NIFTY 50 constituent list from NSE Archives.
    Falls back to a curated stock list if NSE request times out.
    """
    url = "https://archives.nseindia.com/content/indices/ind_nifty50list.csv"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            df = pd.read_csv(io.StringIO(res.text))
            return [f"{sym}.NS" for sym in df['Symbol'].tolist()]
    except Exception:
        pass
    return DEFAULT_NIFTY50_FALLBACK


def _archive_path(symbol: str, interval: str) -> str:
    """
    Returns the local archive CSV path for a ticker.
    Naming convention: market_data/{SYMBOL}_{interval}.csv
      e.g. RELIANCE.NS  -> market_data/RELIANCE_15m.csv
           ^NSEI        -> market_data/NIFTY50_15m.csv
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    clean = symbol.replace(".NS", "").replace("^NSEI", "NIFTY50").replace(".", "_")
    return os.path.join(CACHE_DIR, f"{clean}_{interval}.csv")


def _is_cache_fresh(df: pd.DataFrame) -> bool:
    """
    A local archive is considered fresh if it contains candles up to the
    most recent completed NSE trading session (accounting for weekends and market hours).
    """
    if df is None or df.empty:
        return False
    last_ts = df.index[-1]
    if getattr(last_ts, "tzinfo", None) is not None:
        last_ts = last_ts.tz_localize(None)

    now = pd.Timestamp.now()
    today_date = now.date()
    last_date = pd.Timestamp(last_ts).date()

    # If today is a weekday and current time is past 15:30 IST (session finished)
    if now.weekday() < 5 and (now.hour > 15 or (now.hour == 15 and now.minute >= 30)):
        return last_date == today_date

    # If today is Saturday (5) or Sunday (6), cache must have Friday's date
    if now.weekday() == 5:
        return last_date == (now - pd.Timedelta(days=1)).date()
    elif now.weekday() == 6:
        return last_date == (now - pd.Timedelta(days=2)).date()

    # If today is Monday before market close, cache must have Friday's date
    if now.weekday() == 0:
        return last_date == (now - pd.Timedelta(days=3)).date()

    # Mid-week before market close: cache must have yesterday's date
    return last_date == (now - pd.Timedelta(days=1)).date()


def load_candle_data(
    symbol: str,
    period: str = "60d",
    interval: str = "15m",
    force_refresh: bool = False,
    verbose: bool = True,
) -> Optional[pd.DataFrame]:
    """
    Smart candle loader with multi-tier source resolution:
      1. DuckDB High-Speed Gateway (market_data/openalgo/backtest_data.duckdb):
         Queries multi-month 1m/5m/15m/1h/1d candles instantly with zero network delay.
      2. Local CSV Archive (market_data/{SYMBOL}_{interval}.csv) if fresh.
      3. yfinance Live Network Download with automatic local archiving.
    """
    clean_sym = symbol.replace(".NS", "").replace("^NSEI", "NIFTY50")

    # Tier 1: DuckDB Gateway
    if os.path.exists(settings.DB_PATH) and not force_refresh:
        try:
            table_name = f"ohlcv_{interval}"
            reader = BacktestDataReader()
            raw_df = reader.get_full_dataframe(symbol=clean_sym, table=table_name)
            if raw_df is not None and not raw_df.empty and len(raw_df) >= 50:
                df = raw_df.copy()
                if "symbol" in df.columns:
                    df = df.drop(columns=["symbol"])
                df.columns = [c.capitalize() for c in df.columns]
                if verbose:
                    print(f"  🦆 {symbol}: loaded from DuckDB ({table_name} | {len(df)} candles)")
                return df
        except Exception:
            pass

    # Tier 2: Local CSV Archive
    cache_path = _archive_path(symbol, interval)

    if not force_refresh and os.path.exists(cache_path):
        try:
            df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            if len(df) >= 50 and _is_cache_fresh(df):
                if verbose:
                    print(f"  📂 {symbol}: loaded from archive ({os.path.basename(cache_path)})")
                return df
        except Exception:
            pass

    try:
        yf_symbol = symbol
        if not symbol.startswith("^") and not symbol.endswith(".NS"):
            yf_symbol = f"{symbol}.NS"
        raw_df = yf.download(yf_symbol, period=period, interval=interval, progress=False)
        if raw_df is None or raw_df.empty or len(raw_df) < 50:
            return None

        if isinstance(raw_df.columns, pd.MultiIndex):
            raw_df.columns = raw_df.columns.get_level_values(0)

        raw_df.index.name = "Datetime"
        raw_df.to_csv(cache_path)
        if verbose:
            print(f"  ⬇️  {symbol}: downloaded {len(raw_df)} candles -> archived ({os.path.basename(cache_path)})")
        return raw_df
    except Exception:
        return None


def fetch_nifty_benchmark(
    period: Any = "60d",
    interval: str = "15m",
    force_refresh: bool = False,
    universe: str = "NIFTY50",
    **kwargs
) -> pd.Series:
    cache_key = f"{interval}_{universe}"
    if not force_refresh and cache_key in _BENCHMARK_CACHE:
        return _BENCHMARK_CACHE[cache_key]
    """
    Retrieves Benchmark for Relative Weakness calculation:
      - If DuckDB exists, builds high-fidelity equal-weighted market index over the full DuckDB historical span.
      - Otherwise loads NIFTY 50 Benchmark (^NSEI) from local archive or yfinance.
    """
    # If TradingConfig object passed as first positional arg
    if hasattr(period, 'TIMEFRAME'):
        cfg = period
        interval = getattr(cfg, 'TIMEFRAME', interval)
        period = "60d"
    elif not isinstance(period, str):
        period = "60d"

    """
    Retrieves Benchmark for Relative Weakness calculation:
      - If DuckDB exists, builds high-fidelity equal-weighted market index over the full DuckDB historical span.
      - Otherwise loads NIFTY 50 Benchmark (^NSEI) from local archive or yfinance.
    """
    print("\n[1/3] Fetching Benchmark for Relative Weakness calculation...")
    
    # Tier 1: DuckDB Full Historical Span Index
    if os.path.exists(settings.DB_PATH) and not force_refresh:
        try:
            reader = BacktestDataReader()
            table_name = f"ohlcv_{interval}"
            duck = reader._duck()
            with reader._connect(duck) as conn:
                query = f"""
                    WITH daily_first AS (
                        SELECT 
                            symbol,
                            CAST(timestamp AS DATE) as trade_date,
                            arg_min(open, timestamp) as day_open
                        FROM {table_name}
                        GROUP BY symbol, CAST(timestamp AS DATE)
                    ),
                    bar_pcts AS (
                        SELECT 
                            b.timestamp,
                            (b.close - d.day_open) / d.day_open as pct_change
                        FROM {table_name} b
                        JOIN daily_first d 
                          ON b.symbol = d.symbol 
                         AND CAST(b.timestamp AS DATE) = d.trade_date
                    )
                    SELECT 
                        timestamp,
                        AVG(pct_change) as avg_pct
                    FROM bar_pcts
                    GROUP BY timestamp
                    ORDER BY timestamp
                """
                res_df = conn.execute(query).df()
                if not res_df.empty:
                    res_df['timestamp'] = reader._normalize_timestamp(res_df['timestamp'])
                    res_df = res_df.set_index('timestamp')
                    print(f"  🦆 Benchmark generated dynamically from DuckDB ({table_name} | {len(res_df)} candles)")
                    series = res_df['avg_pct']
                    _BENCHMARK_CACHE[cache_key] = series
                    return series
        except Exception:
            pass

    # Tier 2: Local CSV Archive / yfinance
    nifty_raw = load_candle_data("^NSEI", period=period, interval=interval, force_refresh=force_refresh)
    if nifty_raw is None or nifty_raw.empty:
        print("⚠️ Warning: Could not fetch Nifty index data.")
        return pd.Series()

    nifty_raw = nifty_raw.copy()
    nifty_raw['Date'] = nifty_raw.index.date
    daily_opens = nifty_raw.groupby('Date')['Open'].transform('first')
    nifty_raw['Nifty_Pct'] = (nifty_raw['Close'] - daily_opens) / daily_opens
    return nifty_raw['Nifty_Pct']


def fetch_stock_candles(
    ticker: str,
    period: str = "60d",
    interval: str = "15m",
    use_cache: bool = False,
    force_refresh: bool = False,
    verbose: bool = False,
) -> Optional[pd.DataFrame]:
    """
    Backward-compatible alias for load_candle_data.
    use_cache=True attempts the local archive first (legacy behaviour);
    use_cache=False forces a fresh download (legacy default).
    """
    return load_candle_data(
        ticker,
        period=period,
        interval=interval,
        force_refresh=force_refresh or (not use_cache),
        verbose=verbose,
    )




# -------------------------------------------------------------------------
# Broker-Agnostic OpenAlgo Market Data Gateway
# -------------------------------------------------------------------------

def fetch_openalgo_tick_price(api_client: Any, symbol: str, exchange: str = "NSE") -> Optional[Dict[str, float]]:
    """
    Fetches real-time market tick (LTP, High, Low) through OpenAlgo Unified API.
    Broker-agnostic across 24+ Indian brokers without broker-specific token mappings.
    """
    if api_client is None:
        return None

    clean_sym = symbol.replace('.NS', '').replace('-EQ', '')
    try:
        # Check OpenAlgo quotes method (get_quotes or quotes)
        quotes_fn = getattr(api_client, 'get_quotes', None) or getattr(api_client, 'quotes', None)
        if quotes_fn:
            res = quotes_fn(symbol=clean_sym, exchange=exchange)
            if res and isinstance(res, dict) and res.get('status') == 'success':
                data = res.get('data', res)
                ltp = float(data.get('ltp', data.get('last_price', 0.0)))
                h = float(data.get('high', ltp))
                l = float(data.get('low', ltp))
                if ltp > 0:
                    return {'ltp': ltp, 'high': h, 'low': l}

        # Fallback to get_ltp
        ltp_fn = getattr(api_client, 'get_ltp', None)
        if ltp_fn:
            res = ltp_fn(symbol=clean_sym, exchange=exchange)
            if res and isinstance(res, dict) and res.get('status') == 'success':
                ltp = float(res.get('data', {}).get('ltp', 0.0))
                if ltp > 0:
                    return {'ltp': ltp, 'high': ltp, 'low': ltp}
    except Exception:
        pass
    return None


def fetch_openalgo_candles(
    api_client: Any,
    symbol: str,
    interval: str = "15m",
    exchange: str = "NSE",
    days: int = 5
) -> Optional[pd.DataFrame]:
    """
    Fetches intraday historical candle bars via OpenAlgo historical data gateway.
    """
    if api_client is None:
        return None

    clean_sym = symbol.replace('.NS', '').replace('-EQ', '')
    try:
        hist_fn = getattr(api_client, 'history', None)
        if hist_fn:
            import datetime
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            start_str = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
            res = hist_fn(
                symbol=clean_sym,
                exchange=exchange,
                interval=interval,
                start_date=start_str,
                end_date=today_str
            )
            # OpenAlgo SDK returns either a pandas DataFrame directly or a JSON dict
            if isinstance(res, pd.DataFrame) and not res.empty and len(res) >= 20:
                df = res.copy()
                col_map = {c: c.capitalize() for c in df.columns if c.lower() in ['open', 'high', 'low', 'close', 'volume']}
                if 'time' in df.columns: col_map['time'] = 'timestamp'
                if 'date' in df.columns: col_map['date'] = 'timestamp'
                df.rename(columns=col_map, inplace=True)
                if 'timestamp' in df.columns:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    df.sort_values('timestamp', inplace=True)
                    df.reset_index(drop=True, inplace=True)
                return df
            elif isinstance(res, dict) and res.get('status') == 'success':
                bars = res.get('data', [])
                if bars and len(bars) >= 20:
                    df = pd.DataFrame(bars)
                    col_map = {c: c.capitalize() for c in df.columns if c.lower() in ['open', 'high', 'low', 'close', 'volume']}
                    if 'time' in df.columns: col_map['time'] = 'timestamp'
                    df.rename(columns=col_map, inplace=True)
                    if 'timestamp' in df.columns:
                        df['timestamp'] = pd.to_datetime(df['timestamp'])
                        df.sort_values('timestamp', inplace=True)
                        df.reset_index(drop=True, inplace=True)
                    return df
    except Exception:
        pass
    return None


def fetch_verified_candles(
    ticker: str,
    period: Any = "5d",
    interval: Any = "15m",
    retry_delays: tuple = (0, 3, 5, 7),
    verbose: bool = False,
    api_client: Optional[Any] = None,
    **kwargs
) -> Optional[pd.DataFrame]:
    if hasattr(period, 'TIMEFRAME'):
        interval = getattr(period, 'TIMEFRAME', interval)
        period = "5d"
    elif not isinstance(period, str):
        period = "5d"
    if not isinstance(interval, str):
        interval = "15m"

    """
    Ingests live candle data with resilient multi-attempt retry (0s, 3s, 5s, 7s).
    - Tier 1: Real-time OpenAlgo Gateway (if api_client provided & authenticated).
    - Tier 2: Resilient historical/live local fallback pipeline.
    """
    import time

    # Tier 1: OpenAlgo Unified Live Feed (Broker)
    if api_client is not None:
        try:
            b_df = fetch_openalgo_candles(api_client, ticker, interval=interval)
            if b_df is not None and not b_df.empty and len(b_df) >= 30:
                b_df._data_source = "Shoonya (OpenAlgo Broker Gateway)"
                return b_df
        except Exception:
            pass

    # Tier 2: Fallback Feed (yfinance)
    for delay in retry_delays:
        if delay > 0:
            time.sleep(delay)
        try:
            df = fetch_stock_candles(ticker, period=period, interval=interval, force_refresh=True, verbose=verbose)
            if df is not None and not df.empty and len(df) >= 30:
                df._data_source = "Network Fallback (yfinance)"
                return df
        except Exception:
            continue
    return None


def fetch_latest_tick_price(ticker: str, api_client: Optional[Any] = None) -> Optional[Dict[str, float]]:
    """
    Fetches latest live candle tick (Close, High, Low) for an active position.
    - Tier 1: Native OpenAlgo Gateway (sub-second zero latency).
    - Tier 2: 1m/5m live fallback candle feed.
    """
    # Tier 1: OpenAlgo Live Quote
    if api_client is not None:
        try:
            b_tick = fetch_openalgo_tick_price(api_client, ticker)
            if b_tick is not None and b_tick.get('ltp', 0.0) > 0:
                return b_tick
        except Exception:
            pass

    # Tier 2: Fallback Feed
    try:
        raw = yf.download(ticker, period="1d", interval="1m", progress=False)
        if raw is not None and not raw.empty and len(raw) > 0:
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            latest = raw.iloc[-1]
            return {
                'ltp': float(latest['Close']),
                'high': float(latest['High']),
                'low': float(latest['Low'])
            }
    except Exception:
        pass

    try:
        raw = yf.download(ticker, period="5d", interval="5m", progress=False)
        if raw is not None and not raw.empty and len(raw) > 0:
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            latest = raw.iloc[-1]
            return {
                'ltp': float(latest['Close']),
                'high': float(latest['High']),
                'low': float(latest['Low'])
            }
    except Exception:
        pass

    return None
