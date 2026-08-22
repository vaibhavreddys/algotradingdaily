"""Publish the OHLCV Parquet archive to a private Hugging Face dataset."""
from __future__ import annotations

import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import archive, settings

logger = logging.getLogger(__name__)

DEFAULT_REPO = "vaibhavfury/StockData"


def _load_hf_api(token: str | None = None) -> Any:
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is not installed. Run `pip install -r requirements.txt`."
        ) from exc
    return HfApi(token=token)


def build_dataset_card(repo_id: str) -> str:
    """Render the dataset card shown on the Hugging Face repo page."""
    duckdb_mod = archive._load_duckdb()
    con = duckdb_mod.connect(str(settings.DB_PATH), read_only=True)
    try:
        rows, symbols = con.execute("SELECT COUNT(*), COUNT(DISTINCT symbol) FROM ohlcv_1m").fetchone()
        first_ts, last_ts = con.execute("SELECT MIN(timestamp), MAX(timestamp) FROM ohlcv_1m").fetchone()
    finally:
        con.close()
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""---
language: []
license: other
pretty_name: NSE Intraday OHLCV (OpenAlgo)
tags:
- finance
- nse
- india
- candles
- ohlcv
private: true
---

# NSE Intraday OHLCV — {repo_id}

Private dataset of Indian equity (NSE) **1-minute OHLCV candles** for NIFTY-200
constituents, ingested via OpenAlgo and exported as Hive-partitioned Parquet.

| | |
|---|---|
| Candles | {rows:,} |
| Symbols | {symbols} |
| Coverage (IST) | {first_ts:%Y-%m-%d %H:%M} → {last_ts:%Y-%m-%d %H:%M} |
| Generated | {generated} |

## Layout

```
data/year=<YYYY>/month=<M>/data_*.parquet   # hive-partitioned, ZSTD compressed
tools/hf_stock_data.py                      # standalone teammate helper
```

## Schema

| column | type | notes |
|---|---|---|
| timestamp | TIMESTAMP (UTC instant) | candle open time; **convert to IST for display** |
| open / high / low / close | DOUBLE | INR |
| volume | BIGINT | shares; closing-auction artifacts clipped to >= 0 at ingest |
| exchange | VARCHAR | always `NSE` |
| year, month | partition columns | derived from IST wall-clock |

## Usage

DuckDB remote query (no download; needs an HF token with read access):

```python
import duckdb
con = duckdb.connect()
con.execute("CREATE SECRET (TYPE huggingface, TOKEN 'hf_xxx')")
con.execute("SET TimeZone='Asia/Kolkata'")
df = con.execute(\"\"\"
    SELECT timestamp AT TIME ZONE 'Asia/Kolkata' AS ts_ist, symbol,
           open, high, low, close, volume
    FROM read_parquet('hf://datasets/{repo_id}/data/year=*/month=*/*.parquet',
                      hive_partitioning=true)
    WHERE symbol = 'RELIANCE' AND year = 2026 AND month = 8
    ORDER BY ts_ist
\"\"\").fetchdf()
```

Download locally (see tools/hf_stock_data.py):

```bash
python hf_stock_data.py parquet --out ./stockdata          # full snapshot
python hf_stock_data.py parquet --months 2025-10 2025-11 --out ./stockdata
python hf_stock_data.py duckdb --out ./stockdata           # + builds backtest_data.duckdb
python hf_stock_data.py query --sql "SELECT COUNT(*) FROM read_parquet('{{glob}}', hive_partitioning=true)"
```

Hugging Face datasets streaming:

```python
from datasets import load_dataset
ds = load_dataset("{repo_id}", data_files="data/**/*.parquet", streaming=True, token=True)
```

Rebuild aggregate timeframes from the raw minute data:

```sql
CREATE TABLE ohlcv_15m AS
SELECT time_bucket(INTERVAL '15 minutes', timestamp AT TIME ZONE 'Asia/Kolkata')
       AT TIME ZONE 'Asia/Kolkata' AS timestamp,
       symbol, arg_min(open, timestamp) AS open, max(high) AS high,
       min(low) AS low, arg_max(close, timestamp) AS close, sum(volume) AS volume
FROM ohlcv_1m GROUP BY 1, symbol;
```

## Notice

Exchange data is licensed for internal use only — do not redistribute.
Timestamps are stored as UTC instants; partitions follow IST calendar months.
"""


def publish(repo_id: str = DEFAULT_REPO, token: str | None = None) -> str:
    """Export fresh Parquet, then upload data + card + helper tool to HF."""
    api = _load_hf_api(token)
    api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)

    archive.export_all()
    card = build_dataset_card(repo_id)

    logger.info("Uploading parquet to %s ...", repo_id)
    api.upload_folder(
        folder_path=str(settings.ARCHIVE_DIR),
        repo_id=repo_id,
        repo_type="dataset",
        path_in_repo="data",
        commit_message="Refresh OHLCV parquet snapshot",
    )

    tool_src = settings.PROJECT_ROOT / "scripts" / "hf_stock_data.py"
    if tool_src.exists():
        api.upload_file(
            path_or_fileobj=str(tool_src),
            path_in_repo="tools/hf_stock_data.py",
            repo_id=repo_id,
            repo_type="dataset",
            commit_message="Update teammate helper script",
        )

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(card)
        card_path = Path(fh.name)
    try:
        api.upload_file(
            path_or_fileobj=str(card_path),
            path_in_repo="README.md",
            repo_id=repo_id,
            repo_type="dataset",
            commit_message="Update dataset card",
        )
    finally:
        card_path.unlink(missing_ok=True)

    files = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
    n_parquet = sum(1 for f in files if f.endswith(".parquet"))
    logger.info("Published %d parquet files to https://huggingface.co/datasets/%s", n_parquet, repo_id)
    return repo_id
