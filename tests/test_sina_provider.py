import unittest
from unittest.mock import Mock, patch

from market_data.providers.sina_provider import SinaProvider


class SinaProviderTest(unittest.TestCase):
    def test_parses_jsonp_payload(self):
        payload = '/*prefix*/ var data=([{"day":"2026-07-14 10:00:00","close":"1.23"}])'

        rows = SinaProvider._parse_jsonp(payload)

        self.assertEqual("1.23", rows[0]["close"])

    def test_normalizes_exchange_suffix(self):
        self.assertEqual("sh512980", SinaProvider._normalize_symbol("512980.SH"))
        self.assertEqual("sz000001", SinaProvider._normalize_symbol("000001.SZ"))

    def test_supports_a_share_daily_symbols(self):
        self.assertIn("D", SinaProvider.supported_periods)
        self.assertTrue(SinaProvider.supports_symbol("000001.SH"))
        self.assertFalse(SinaProvider.supports_symbol("HSI.HK"))

    def test_parses_realtime_quote_as_current_daily_bar(self):
        provider = SinaProvider()
        response = Mock()
        fields = ["上证指数", "3912.38", "3955.58", "3934.94", "3940.45", "3902.70"]
        fields.extend(["0"] * 24)
        fields.extend(["2026-07-16", "10:50:44", "00"])
        response.content = f'var hq_str_sh000001="{",".join(fields)}";'.encode("gb18030")
        try:
            with patch.object(provider, "_request", return_value=response):
                bar = provider._fetch_realtime_daily("000001.SH")
        finally:
            provider.close()

        self.assertEqual("2026-07-16", bar["trade_time"])
        self.assertEqual("3934.94", bar["close"])


if __name__ == "__main__":
    unittest.main()
