import unittest

import pandas as pd

from market_data.providers.sge_provider import ShanghaiGoldExchangeProvider


class ShanghaiGoldExchangeProviderTest(unittest.TestCase):
    def test_supports_only_project_spot_gold_symbol(self):
        self.assertTrue(ShanghaiGoldExchangeProvider.supports_symbol("GOLD.SGE"))
        self.assertFalse(ShanghaiGoldExchangeProvider.supports_symbol("GOLD.CMD"))

    def test_parses_au9999_ohlc_history(self):
        rows = [
            ["2026-07-14", 885.3, 879.9, 872.0, 889.0],
            ["2026-07-15", 882.0, 877.99, 876.8, 895.0],
        ]

        data = ShanghaiGoldExchangeProvider._parse_history(rows, "2026-07-14", "2026-07-15")

        self.assertEqual(2, len(data))
        self.assertEqual(pd.Timestamp("2026-07-15"), data.iloc[-1]["trade_time"])
        self.assertEqual(895.0, data.iloc[-1]["high"])
        self.assertEqual(0.0, data.iloc[-1]["vol"])
        self.assertLess(data.iloc[-1]["pct_chg"], 0)


if __name__ == "__main__":
    unittest.main()
