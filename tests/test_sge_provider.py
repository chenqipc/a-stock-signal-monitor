import unittest
from unittest.mock import Mock

import pandas as pd
import requests

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

    def test_falls_back_to_direct_connection_when_proxy_is_unavailable(self):
        provider = ShanghaiGoldExchangeProvider()
        response = Mock()
        response.json.return_value = {"time": [["2026-07-16", 880.0, 888.0, 878.0, 890.0]]}
        provider.session.post = Mock(side_effect=requests.exceptions.InvalidSchema("SOCKS依赖缺失"))
        provider.direct_session.post = Mock(return_value=response)
        provider.session.get = Mock(side_effect=requests.exceptions.InvalidSchema("SOCKS依赖缺失"))
        provider.direct_session.get = Mock(side_effect=requests.exceptions.ConnectionError("延时接口不可用"))
        try:
            data = provider.fetch_bars("GOLD.SGE", "D", "2026-07-16", "2026-07-16")
        finally:
            provider.close()

        self.assertEqual(1, len(data))
        self.assertEqual(888.0, data.iloc[0]["close"])
        provider.direct_session.post.assert_called_once()

    def test_parses_delayed_au9999_quote(self):
        content = """
        <h1>上海黄金交易所2026年07月17日延时行情</h1>
        <tr class="ininfo"><td>Au99.99</td><td>870.61</td><td>882.5</td><td>867.0</td><td>875.0</td></tr>
        """

        quote = ShanghaiGoldExchangeProvider._parse_delayed_quote(content)

        self.assertEqual("2026-07-17", quote["trade_time"])
        self.assertEqual("870.61", quote["close"])
        self.assertEqual("875.0", quote["open"])


if __name__ == "__main__":
    unittest.main()
