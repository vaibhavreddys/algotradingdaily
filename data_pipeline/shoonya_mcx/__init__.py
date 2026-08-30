"""Shoonya (Noren) MCX commodity historical-data ingestion.

Downloads 1-minute candles for active MCX futures contracts directly from
the Shoonya (Finvasia) REST API via ``NorenRestApiPy`` and upserts them into
a local DuckDB store (``market_data/shoonya_mcx/mcx_historical_data.duckdb``).

Run through the CLI::

    python -m data_pipeline.shoonya_mcx --action download --symbols all
    python -m data_pipeline.shoonya_mcx --action stats

The layout mirrors ``data_pipeline.openalgo_ingestion`` so a future
forex module can follow the exact same pattern.
"""

from .downloader import MCXIngestionEngine
from .settings import DB_PATH, validate_settings

__all__ = ["MCXIngestionEngine", "DB_PATH", "validate_settings"]
