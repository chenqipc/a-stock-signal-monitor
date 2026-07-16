import sys
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from market_data.providers.tushare_provider import TushareProvider


class TushareProviderTest(unittest.TestCase):
    def setUp(self):
        TushareProvider._request_times.clear()
        TushareProvider._daily_request_date = date.today()
        TushareProvider._daily_request_count = 0
        TushareProvider._token_cursor = 0

    def test_rotates_tokens_under_shared_rate_limit(self):
        provider = TushareProvider(["token-a", "token-b"], requests_per_minute=10)

        self.assertEqual("token-a", provider._acquire_token())
        self.assertEqual("token-b", provider._acquire_token())
        self.assertEqual(2, TushareProvider._daily_request_count)

    def test_free_daily_data_units_are_normalized(self):
        api = Mock()
        api.daily.return_value = pd.DataFrame([{
            "trade_date": "20260715", "open": 10, "high": 11, "low": 9, "close": 10.5, "pre_close": 10,
            "pct_chg": 5, "vol": 1234, "amount": 5678,
        }])
        fake_tushare = SimpleNamespace(pro_api=Mock(return_value=api))
        provider = TushareProvider(["token-a"], requests_per_minute=10)

        with patch.dict(sys.modules, {"tushare": fake_tushare}):
            data = provider.fetch_bars("600000.SH", "D", "2026-07-01", "2026-07-16")

        self.assertEqual(123400, data.iloc[0]["vol"])
        self.assertEqual(5678000, data.iloc[0]["amount"])
        fake_tushare.pro_api.assert_called_once_with("token-a")

    def test_only_stock_symbols_use_free_daily_endpoint(self):
        self.assertTrue(TushareProvider.supports_symbol("600000.SH"))
        self.assertTrue(TushareProvider.supports_symbol("000001.SZ"))
        self.assertFalse(TushareProvider.supports_symbol("510300.SH"))
        self.assertFalse(TushareProvider.supports_symbol("000001.SH"))
        self.assertFalse(TushareProvider.supports_symbol("HSI.HK"))


if __name__ == "__main__":
    unittest.main()
