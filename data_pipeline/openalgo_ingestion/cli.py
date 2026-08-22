"""Command-line interface for OpenAlgo historical ingestion."""

import argparse
import logging
import sys

from . import settings
from .downloader import ThrottledIngestionEngine
from .reader import BacktestDataReader
from .scraper import NSEConstituentFetcher


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenAlgo historical-data ingestion")
    parser.add_argument("--action", choices=("download", "read", "scrape", "stats", "archive", "aggregate", "health", "publish"), required=True)
    parser.add_argument("--repo", help="HF dataset repo for publish (default $OPENALGO_HF_REPO or vaibhavfury/StockData)")
    parser.add_argument("--index", help="Live NSE index, e.g. NIFTY50 or NIFTY200")
    parser.add_argument("--symbols", help="Comma-separated NSE symbols, e.g. RELIANCE,TCS,INFY")
    parser.add_argument("--limit", type=int, default=0, help="Limit the selected universe")
    parser.add_argument("--days", type=int, default=365, help="Lookback days when no dates are specified")
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD); requires --end-date")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD); requires --start-date")
    parser.add_argument("--refresh-boundary", action="store_true",
                        help="Re-detect the earliest date the broker serves before downloading")
    return parser


def setup_logging() -> None:
    settings.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[logging.FileHandler(settings.LOG_PATH), logging.StreamHandler(sys.stdout)],
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    logger = logging.getLogger("openalgo_ingestion")

    if args.limit < 0:
        logger.error("--limit cannot be negative")
        return 2
    if bool(args.start_date) != bool(args.end_date):
        logger.error("--start-date and --end-date are required together")
        return 2

    if args.action == "health":
        from .health import run_health_check

        report = run_health_check()
        logger.info("\n%s", report.summary())
        return 0 if report.healthy else 1

    if args.action == "scrape":
        registry = NSEConstituentFetcher.get_registry()
        for index, symbols in registry.items():
            logger.info("%s: %d constituents", index, len(symbols))
        return 0 if any(registry.values()) else 1

    if args.action == "read":
        try:
            frame = BacktestDataReader().get_full_dataframe(
                start_date=args.start_date, end_date=args.end_date
            )
        except RuntimeError as exc:
            logger.error("%s", exc)
            return 2
        logger.info("Loaded %d rows from %s", len(frame), settings.DB_PATH)
        if not frame.empty:
            logger.info("%s", frame.head())
        return 0

    if args.action == "stats":
        stats = BacktestDataReader().get_stats()
        logger.info("Summary:\n%s", stats["summary"])
        logger.info("Monthly (last 12):\n%s", stats["monthly"].tail(12))
        return 0

    if args.action == "archive":
        from . import archive

        logger.info("Starting archival export...")
        archive.export_all()
        logger.info("Archive contains %d rows", archive.count_archive_rows())
        return 0

    if args.action == "aggregate":
        from . import archive

        logger.info("Building aggregated timeframe tables...")
        archive.build_all_aggregates()
        logger.info("Aggregates built successfully.")
        return 0

    if args.action == "publish":
        from .publish import DEFAULT_REPO, publish

        publish(args.repo or DEFAULT_REPO)
        return 0

    try:
        settings.validate_settings()
        if args.symbols:
            symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
        elif args.index:
            symbols = NSEConstituentFetcher.get_index_symbols(args.index)
        else:
            raise ValueError("--index is required unless --symbols is provided")
        if not symbols:
            raise ValueError("No symbols were selected")
        if args.limit:
            symbols = symbols[:args.limit]
        engine = ThrottledIngestionEngine()
        if args.refresh_boundary:
            engine.detect_history_start(force=True)
        else:
            engine.detect_history_start()
        if args.start_date:
            engine.ingest_date_range(symbols, args.start_date, args.end_date)
        else:
            engine.ingest_index(symbols, args.days)
    except (RuntimeError, ValueError) as exc:
        logger.error("%s", exc)
        return 2
    return 0
