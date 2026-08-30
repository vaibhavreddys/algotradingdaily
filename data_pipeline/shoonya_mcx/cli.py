"""Command-line interface for Shoonya MCX historical ingestion."""

import argparse
import logging
import sys

from . import settings
from .downloader import MCXIngestionEngine
from .symbols import MCX_COMMODITIES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shoonya MCX 1-minute historical ingestion")
    parser.add_argument("--action", choices=("download", "stats"), default="download")
    parser.add_argument(
        "--symbols",
        help="Comma-separated MCX commodities (e.g. GOLD,SILVER,CRUDEOIL) or 'all'.",
    )
    parser.add_argument("--start-date", help="Explicit history start (YYYY-MM-DD); skips boundary search")
    parser.add_argument("--end-date", help="History end date (YYYY-MM-DD); defaults to today")
    parser.add_argument(
        "--refresh-boundary",
        action="store_true",
        help="Re-run the binary search even when a cached boundary exists",
    )
    return parser


def setup_logging() -> None:
    settings.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[logging.FileHandler(settings.LOG_PATH), logging.StreamHandler(sys.stdout)],
    )


def resolve_symbols(raw: str | None) -> list[str]:
    if not raw or raw.strip().lower() == "all":
        return list(MCX_COMMODITIES)
    symbols = [token.strip().upper() for token in raw.split(",") if token.strip()]
    unknown = [symbol for symbol in symbols if symbol not in MCX_COMMODITIES]
    if unknown:
        raise ValueError(
            f"Unknown commodities: {', '.join(unknown)}. Known: {', '.join(MCX_COMMODITIES)}"
        )
    return symbols


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    logger = logging.getLogger("shoonya_mcx")

    if bool(args.start_date) and bool(args.end_date) and args.start_date > args.end_date:
        logger.error("--start-date cannot be after --end-date")
        return 2

    try:
        engine = MCXIngestionEngine()
        if args.action == "stats":
            frame = engine.stats()
            if frame.empty:
                logger.info("No data ingested yet.")
            else:
                logger.info("Coverage:\n%s", frame.to_string(index=False))
            return 0

        symbols = resolve_symbols(args.symbols)
        rows = engine.run(
            commodities=symbols,
            start_date=args.start_date,
            end_date=args.end_date,
            refresh_boundary=args.refresh_boundary,
        )
        logger.info("Ingestion finished with %d rows upserted.", rows)
    except (RuntimeError, ValueError, KeyError) as exc:
        logger.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
