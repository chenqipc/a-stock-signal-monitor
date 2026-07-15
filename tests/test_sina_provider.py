import unittest

from market_data.providers.sina_provider import SinaProvider


class SinaProviderTest(unittest.TestCase):
    def test_parses_jsonp_payload(self):
        payload = '/*prefix*/ var data=([{"day":"2026-07-14 10:00:00","close":"1.23"}])'

        rows = SinaProvider._parse_jsonp(payload)

        self.assertEqual("1.23", rows[0]["close"])

    def test_normalizes_exchange_suffix(self):
        self.assertEqual("sh512980", SinaProvider._normalize_symbol("512980.SH"))
        self.assertEqual("sz000001", SinaProvider._normalize_symbol("000001.SZ"))


if __name__ == "__main__":
    unittest.main()
