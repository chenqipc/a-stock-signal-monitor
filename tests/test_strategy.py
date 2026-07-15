import unittest

import pandas as pd

from etf_monitor.ma_monitor import detect_ma_crosses
from stock_signal_monitor.stock_strategy import (
    get_price_limit_ratio,
    is_limit_up_3days,
    is_limit_up_only_3days,
    is_stock_stabilizing_over60,
)


class StrategyTest(unittest.TestCase):
    def test_price_limit_ratio_is_board_aware(self):
        self.assertEqual(0.10, float(get_price_limit_ratio("600000.SH")))
        self.assertEqual(0.20, float(get_price_limit_ratio("300001.SZ")))
        self.assertEqual(0.30, float(get_price_limit_ratio("830001.BJ")))
        self.assertEqual(0.05, float(get_price_limit_ratio("600000.SH", is_st=True)))

    def test_exactly_three_limit_ups_only_checks_the_previous_day(self):
        data = pd.DataFrame(
            {
                "pre_close": [10.00, 10.10, 11.11, 12.22, 13.44],
                "close": [10.10, 11.11, 12.22, 13.44, 14.78],
                "pct_chg": [1.0, 10.0, 10.0, 10.0, 10.0],
                "is_st": [0, 0, 0, 0, 0],
            }
        )
        self.assertTrue(is_limit_up_3days(data, "600000.SH"))
        self.assertFalse(is_limit_up_only_3days(data, "600000.SH"))
        data.loc[1, "close"] = 10.50
        self.assertTrue(is_limit_up_only_3days(data, "600000.SH"))

    def test_ma_cross_uses_previous_bar_previous_average(self):
        data = pd.DataFrame(
            {
                "trade_time": pd.to_datetime(["2026-07-14 10:00", "2026-07-14 10:15"]),
                "close": [10.0, 10.2],
                "MA10": [9.5, 10.1],
                "MA30": [10.2, 10.1],
                "MA60": [9.9, 10.0],
            }
        )
        crosses = detect_ma_crosses(data)
        self.assertIsNone(crosses[10])
        self.assertEqual("up", crosses[30])
        self.assertIsNone(crosses[60])

    def test_breakout_over_ma60_requires_a_real_cross(self):
        closes = [10.0] * 59 + [9.0, 10.1]
        data = pd.DataFrame({"close": closes, "vol": [1000.0] * len(closes)})
        self.assertTrue(is_stock_stabilizing_over60(data))
        data.loc[len(data) - 2, "close"] = 10.2
        self.assertFalse(is_stock_stabilizing_over60(data))


if __name__ == "__main__":
    unittest.main()
