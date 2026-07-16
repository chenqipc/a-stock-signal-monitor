import unittest
from unittest.mock import patch

from market_data.providers.yahoo_provider import YahooFinanceProvider


class YahooFinanceProviderTest(unittest.TestCase):
    def test_supports_only_registered_global_indices(self):
        self.assertTrue(YahooFinanceProvider.supports_symbol("HSI.HK"))
        self.assertTrue(YahooFinanceProvider.supports_symbol("IXIC.US"))
        self.assertFalse(YahooFinanceProvider.supports_symbol("GOLD.SGE"))
        self.assertFalse(YahooFinanceProvider.supports_symbol("000001.SH"))

    def test_maps_project_symbols_to_yahoo_symbols(self):
        provider = YahooFinanceProvider()
        empty_payload = {"chart": {"result": [], "error": None}}
        try:
            with patch.object(provider, "_get_json", return_value=empty_payload) as request:
                with self.assertRaises(Exception):
                    provider.fetch_bars("IXIC.US", "D", "2026-07-01", "2026-07-16")
                request.assert_called_once_with("^IXIC", "2026-07-01", "2026-07-16")
        finally:
            provider.close()

    def test_parses_hsi_daily_chart(self):
        payload = {
            "chart": {
                "result": [{
                    "timestamp": [1783987200, 1784073600, 1784160000],
                    "indicators": {"quote": [{
                        "open": [24100.0, None, 24200.0],
                        "close": [24200.0, None, 24320.0],
                        "high": [24300.0, None, 24400.0],
                        "low": [24000.0, None, 24100.0],
                        "volume": [100.0, None, 120.0],
                    }]},
                }],
                "error": None,
            }
        }
        provider = YahooFinanceProvider()
        try:
            with patch.object(provider, "_get_json", return_value=payload):
                data = provider.fetch_bars("HSI.HK", "D", "2026-07-14", "2026-07-16")
        finally:
            provider.close()

        self.assertEqual(2, len(data))
        self.assertEqual(24320.0, data.iloc[-1]["close"])
        self.assertGreater(data.iloc[-1]["pct_chg"], 0)


if __name__ == "__main__":
    unittest.main()
