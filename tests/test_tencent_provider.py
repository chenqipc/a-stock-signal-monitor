import unittest
from unittest.mock import Mock, patch

from market_data.providers.tencent_provider import TencentProvider


class TencentProviderTest(unittest.TestCase):
    def test_normalizes_exchange_suffix(self):
        self.assertEqual("sh600000", TencentProvider._normalize_symbol("600000.SH"))
        self.assertEqual("sz000001", TencentProvider._normalize_symbol("SZ.000001"))

    def test_supports_shenzhen_and_shanghai_only(self):
        self.assertTrue(TencentProvider.supports_symbol("600000.SH"))
        self.assertTrue(TencentProvider.supports_symbol("SZ.000001"))
        self.assertFalse(TencentProvider.supports_symbol("HSI.HK"))
        self.assertFalse(TencentProvider.supports_symbol("BJ.920001"))

    def test_fetches_history_and_appends_realtime_daily_bar(self):
        provider = TencentProvider()
        payload = {"data": {"sh600000": {"qfqday": [["2026-07-15", "10", "10.2", "10.3", "9.9", "1234"]]}}}
        realtime = {
            "trade_time": "20260716", "open": "10.2", "close": "10.5", "high": "10.6", "low": "10.1", "vol": 200000,
            "amount": 2100000, "pre_close": "10.2", "pct_chg": "2.94", "turnover_rate": "0.5",
        }
        try:
            with patch.object(provider, "_request_json", return_value=payload), patch.object(
                provider, "_fetch_realtime_daily", return_value=realtime
            ):
                data = provider.fetch_bars("600000.SH", "D", "2026-07-01", "2026-07-16")
        finally:
            provider.close()

        self.assertEqual(2, len(data))
        self.assertEqual(123400, data.iloc[0]["vol"])
        self.assertEqual(10.5, data.iloc[-1]["close"])

    def test_parses_realtime_quote_units(self):
        provider = TencentProvider()
        fields = [""] * 50
        fields[3], fields[4], fields[5] = "10.5", "10.2", "10.3"
        fields[6], fields[30], fields[32] = "1234", "20260716103500", "2.94"
        fields[33], fields[34], fields[36], fields[37], fields[38] = "10.6", "10.1", "1234", "129.5", "0.5"
        response = Mock()
        response.content = f'v_sh600000="{"~".join(fields)}";'.encode("gb18030")
        try:
            with patch.object(provider, "_request_text", return_value=response):
                bar = provider._fetch_realtime_daily("sh600000")
        finally:
            provider.close()

        self.assertEqual("20260716", bar["trade_time"])
        self.assertEqual(123400, bar["vol"])
        self.assertEqual(1295000, bar["amount"])


if __name__ == "__main__":
    unittest.main()
