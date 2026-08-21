"""Compatibility launcher for the OpenAlgo historical-ingestion CLI."""

from data_pipeline.openalgo_ingestion.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
