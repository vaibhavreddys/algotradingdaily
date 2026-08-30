"""MCX commodity registry for Shoonya historical ingestion.

Shoonya's TPSeries endpoint addresses instruments by numeric *token* (the
scrip code returned by ``SearchScrip``), not by trading symbol. Active
futures contracts also roll over every month, so the registry below maps
each commodity to a search pattern and lets the engine resolve the current
front-month contract token at runtime. A ``token`` entry can be pinned
manually when a specific (e.g. continuous-contract style) scrip is wanted.
"""

import re
from typing import Any, Mapping

# commodity name -> resolution configuration
#   exchange    : Shoonya exchange segment code
#   search_text : text passed to SearchScrip (Shoonya's symbol stem)
#   token       : optional pinned scrip token; skips runtime resolution
MCX_COMMODITIES: dict[str, dict[str, Any]] = {
    "GOLD":        {"exchange": "MCX", "search_text": "GOLD"},
    "SILVER":      {"exchange": "MCX", "search_text": "SILVER"},
    "CRUDEOIL":    {"exchange": "MCX", "search_text": "CRUDEOIL"},
    "CRUDEOILM":   {"exchange": "MCX", "search_text": "CRUDEOIL-M"},
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


def resolve_active_token(api: Any, commodity: str, config: Mapping[str, Any]) -> str:
    """Return the Shoonya scrip token for the front-month future of a commodity.

    Uses ``SearchScrip`` to list matching contracts, keeps futures whose
    trading symbol is the stem followed by an expiry (e.g. ``GOLD25SEPFUT``,
    ``CRUDEOIL-M25SEPFUT``), and picks the nearest expiry. A pinned
    ``token`` in the registry bypasses resolution entirely.
    """
    pinned = config.get("token")
    if pinned:
        return str(pinned)

    exchange = config["exchange"]
    search_text = str(config["search_text"]).upper()
    response = api.searchscrip(exchange=exchange, searchtext=search_text)
    if not response or response.get("stat") != "Ok":
        raise SymbolResolutionError(
            f"SearchScrip failed for {commodity} ({exchange}:{search_text})"
        )

    stem_pattern = re.compile(rf"^{re.escape(search_text)}[-]?\d")
    futures = [
        scrip
        for scrip in response.get("scrip", [])
        if scrip.get("instname") == "FUT"
        and stem_pattern.match(str(scrip.get("tsym", "")).upper())
    ]
    if not futures:
        raise SymbolResolutionError(
            f"No active futures contract found for {commodity} ({exchange}:{search_text})"
        )

    # Sort by expiry date (ISO strings sort chronologically); undated scrips last.
    futures.sort(key=lambda scrip: str(scrip.get("expiry") or "9999-12-31"))
    chosen = futures[0]
    return str(chosen["token"])
