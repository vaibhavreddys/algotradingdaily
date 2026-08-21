import unittest
from unittest.mock import MagicMock, patch

from data_pipeline.openalgo_ingestion.scraper import NSEConstituentFetcher


class TestNSEConstituentFetcher(unittest.TestCase):
    def _response(self, text: str):
        response = MagicMock()
        response.text = text
        response.raise_for_status.return_value = None
        return response

    @patch.object(NSEConstituentFetcher, "_get_session")
    def test_fetches_and_normalizes_symbols(self, get_session):
        get_session.return_value.get.return_value = self._response("Symbol,Company Name\nINFY,Infosys\nreliance,Reliance\n")
        self.assertEqual(NSEConstituentFetcher.get_index_symbols("nifty50"), ["INFY", "RELIANCE"])

    def test_rejects_unsupported_index(self):
        with self.assertRaisesRegex(ValueError, "Unsupported index"):
            NSEConstituentFetcher.get_index_symbols("BANKNIFTY")

    @patch.object(NSEConstituentFetcher, "_fetch_single_index")
    def test_nifty200_uses_live_component_fallback(self, fetch):
        fetch.side_effect = [[], ["INFY", "RELIANCE"], ["RELIANCE", "TCS"]]
        self.assertEqual(NSEConstituentFetcher.get_index_symbols("NIFTY200"), ["INFY", "RELIANCE", "TCS"])

    @patch.object(NSEConstituentFetcher, "_fetch_single_index", return_value=[])
    def test_network_failure_returns_no_symbols(self, _fetch):
        self.assertEqual(NSEConstituentFetcher.get_index_symbols("NIFTY50"), [])
