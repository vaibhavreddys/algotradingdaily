"""MCX commodity registry for Shoonya historical ingestion.

Shoonya's TPSeries endpoint addresses instruments by numeric *token* (the
scrip code in the official scrip master), not by trading symbol. Active
futures contracts also roll over every month, so the registry below maps
each commodity to a ``Symbol`` stem in the MCX scrip master and the engine
resolves the current front-month contract token at runtime. A ``token``
entry can be pinned manually when a specific scrip is wanted.

The token source is Shoonya's official ``MCX_symbols.txt.zip`` scrip master.
The interactive ``SearchScrip`` endpoint answers "Exchange Not enabled" for
MCX on retail OAuth apps even though TPSeries/GetQuotes serve MCX fine, so
it is no longer used.
"""

import csv
import datetime as dt
import io
import time
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import requests

# commodity name -> resolution configuration
#   exchange    : Shoonya exchange segment code
#   search_text : ``Symbol`` stem in the MCX scrip master (exact match)
#   token       : optional pinned scrip token; skips runtime resolution
MCX_COMMODITIES: dict[str, dict[str, Any]] = {
    "GOLD":        {"exchange": "MCX", "search_text": "GOLD"},
    "SILVER":      {"exchange": "MCX", "search_text": "SILVER"},
    "CRUDEOIL":    {"exchange": "MCX", "search_text": "CRUDEOIL"},
    "CRUDEOILM":   {"exchange": "MCX", "search_text": "CRUDEOILM"},
    "NATURALGAS":  {"exchange": "MCX", "search_text": "NATURALGAS"},
    "COPPER":      {"exchange": "MCX", "search_text": "COPPER"},
    "ALUMINIUM":   {"exchange": "MCX", "search_text": "ALUMINIUM"},
    "ZINC":        {"exchange": "MCX", "search_text": "ZINC"},
    "LEAD":        {"exchange": "MCX", "search_text": "LEAD"},
    "NICKEL":      {"exchange": "MCX", "search_text": "NICKEL"},
    "MENTHAOIL":   {"exchange": "MCX", "search_text": "MENTHAOIL"},
    "GUARGUM":     {"exchange": "MCX", "search_text": "GUARGUM"},
    "JPATBASTI":   {"exchange": "MCX", "search_text": "JPATBASTI"},
}


class SymbolResolutionError(RuntimeError):
    """No tradable MCX scrip could be resolved for a commodity."""


class ScripMasterCache:
    """Download-and-cache wrapper for Shoonya's official MCX scrip master.

    The file is a zip of a CSV whose rows carry at least
    ``Exchange,Token,Symbol,TradingSymbol,Expiry,Instrument``. Refreshed when
    older than ``max_age_hours`` so front-month rolls are picked up.
    """

    def __init__(self, url: str, cache_path: Path, max_age_hours: float = 24.0) -> None:
        self._url = url
        self._cache_path = cache_path
        self._max_age_hours = max_age_hours
        self._rows: list[dict[str, str]] | None = None

    def rows(self) -> list[dict[str, str]]:
        if self._rows is None:
            csv_path = self._refreshed_csv_path()
            self._rows = self._parse_csv(csv_path)
        return self._rows

    def _refreshed_csv_path(self) -> Path:
        stale = (
            not self._cache_path.is_file()
            or time.time() - self._cache_path.stat().st_mtime > self._max_age_hours * 3600
        )
        if stale:
            self._download()
        return self._cache_path

    def _download(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(self._url, timeout=60)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            member = next(
                name for name in archive.namelist() if name.endswith(".txt")
            )
            self._cache_path.write_bytes(archive.read(member))

    @staticmethod
    def _parse_csv(csv_path: Path) -> list[dict[str, str]]:
        with csv_path.open(newline="") as handle:
            return [row for row in csv.DictReader(handle) if row.get("Token")]


def _expiry_key(row: Mapping[str, str]) -> dt.date:
    try:
        return dt.datetime.strptime(row["Expiry"], "%d-%b-%Y").date()
    except (KeyError, ValueError):
        return dt.date(9999, 12, 31)


def resolve_active_token(
    commodity: str,
    config: Mapping[str, Any],
    scrip_rows: list[dict[str, str]],
    today: dt.date | None = None,
) -> str:
    """Return the Shoonya scrip token for the front-month future of a commodity.

    Keeps ``FUTCOM`` rows whose ``Symbol`` equals the configured stem (with a
    ``TradingSymbol`` prefix fallback), prefers the nearest expiry that has
    not lapsed, and falls back to the nearest expiry overall so delisted
    stems still resolve to their final contract for historical pulls.
    """
    pinned = config.get("token")
    if pinned:
        return str(pinned)

    search_text = str(config["search_text"]).upper()
    candidates = [
        row for row in scrip_rows
        if row.get("Instrument") == "FUTCOM"
        and (
            str(row.get("Symbol", "")).upper() == search_text
            or str(row.get("TradingSymbol", "")).upper().startswith(search_text)
        )
    ]
    if not candidates:
        raise SymbolResolutionError(
            f"No MCX futures scrip found for {commodity} (Symbol={search_text})"
        )

    today = today or dt.date.today()
    exact = [row for row in candidates if str(row.get("Symbol", "")).upper() == search_text]
    # Exact Symbol stems win (GOLD must not leak into GOLDM/GOLDGUINEA);
    # the prefix fallback only serves stems the master spells differently.
    pool = exact or candidates
    live = [row for row in pool if _expiry_key(row) >= today]
    chosen = min(live or pool, key=_expiry_key)
    return str(chosen["Token"])
