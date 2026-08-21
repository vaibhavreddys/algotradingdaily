"""Live NSE index-constituent retrieval."""

import logging
from io import StringIO

import pandas as pd
import requests

logger = logging.getLogger(__name__)


class NSEConstituentFetcher:
    """Fetch index membership from NSE archives without a static local registry."""

    DIRECT_INDEX_MAPPING = {
        "NIFTY50": "NIFTY 50",
        "NIFTY100": "NIFTY 100",
        "NIFTY200": "NIFTY 200",
        "NIFTY_MIDCAP_50": "NIFTY MIDCAP 50",
        "NIFTY_MIDCAP_100": "NIFTY MIDCAP 100",
        "NIFTY_SMALLCAP_100": "NIFTY SMALLCAP 100",
        "NIFTY_SMALLCAP_250": "NIFTY SMALLCAP 250",
    }
    CSV_URL_MAPPING = {
        "NIFTY 50": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
        "NIFTY 100": "https://archives.nseindia.com/content/indices/ind_nifty100list.csv",
        "NIFTY 200": "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
        "NIFTY MIDCAP 50": "https://archives.nseindia.com/content/indices/ind_niftymidcap50list.csv",
        "NIFTY MIDCAP 100": "https://archives.nseindia.com/content/indices/ind_niftymidcap100list.csv",
        "NIFTY SMALLCAP 100": "https://archives.nseindia.com/content/indices/ind_niftysmallcap100list.csv",
        "NIFTY SMALLCAP 250": "https://archives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
    }

    @staticmethod
    def _get_session() -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            "Accept": "text/csv,application/csv,*/*;q=0.8",
            "Referer": "https://www.nseindia.com/indices",
        })
        return session

    @classmethod
    def _fetch_single_index(cls, index_name: str) -> list[str]:
        url = cls.CSV_URL_MAPPING[index_name]
        try:
            response = cls._get_session().get(url, timeout=15)
            response.raise_for_status()
            frame = pd.read_csv(StringIO(response.text))
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Could not retrieve %s constituents: %s", index_name, exc)
            return []
        if "Symbol" not in frame.columns:
            logger.warning("NSE response for %s lacks a Symbol column", index_name)
            return []
        return sorted({str(symbol).strip().upper() for symbol in frame["Symbol"].dropna() if str(symbol).strip()})

    @classmethod
    def get_index_symbols(cls, index_key: str) -> list[str]:
        key = index_key.strip().upper()
        if key not in cls.DIRECT_INDEX_MAPPING:
            raise ValueError(f"Unsupported index: {index_key}")
        direct = cls._fetch_single_index(cls.DIRECT_INDEX_MAPPING[key])
        if direct:
            return direct
        if key == "NIFTY200":
            fallback = set(cls._fetch_single_index("NIFTY 100")) | set(cls._fetch_single_index("NIFTY MIDCAP 100"))
            if fallback:
                logger.warning("Official NIFTY 200 list unavailable; using live NIFTY100 + NIFTY_MIDCAP_100")
                return sorted(fallback)
        return []

    @classmethod
    def get_registry(cls) -> dict[str, list[str]]:
        return {key: cls.get_index_symbols(key) for key in cls.DIRECT_INDEX_MAPPING}
