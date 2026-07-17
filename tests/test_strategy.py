import unittest

import pandas as pd

from etf_monitor.ma_monitor import detect_ma_crosses
from stock_signal_monitor.stock_strategy import (
    evaluate_daily_strategies,
    get_price_limit_ratio,
    is_breakout_after_consolidation,
    is_capital_inflow,
    is_double_bottom,
    is_double_bottom_new,
    is_funds_inflow_by_volume_turnover,
    is_limit_up_3days,
    is_limit_up_only_3days,
    is_macd_golden_cross,
    is_macd_golden_cross_7,
    is_rising_with_volume_increase,
    is_stock_stabilizing_over60,
)
from stock_signal_monitor.stock_status import StockStatus


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

    def test_rising_volume_and_capital_inflow_require_consistent_recent_strength(self):
        rising = pd.DataFrame({"pct_chg": [0, 1, 1, 1], "vol": [90, 100, 105, 110]})
        capital = pd.DataFrame({"amount": [100] * 7 + [125, 130, 135], "pct_chg": [0] * 7 + [1, 1, 1]})

        self.assertTrue(is_rising_with_volume_increase(rising))
        self.assertTrue(is_capital_inflow(capital))
        capital.loc[9, "pct_chg"] = -1
        self.assertFalse(is_capital_inflow(capital))

    def test_breakout_after_consolidation_requires_price_and_volume_expansion(self):
        data = pd.DataFrame({"close": [10] * 30 + [10, 10.1, 10.2, 10.3, 10.5], "vol": [100] * 30 + [130] * 5})

        self.assertTrue(is_breakout_after_consolidation(data))
        data.loc[30:, "vol"] = 105
        self.assertFalse(is_breakout_after_consolidation(data))

    def test_volume_turnover_inflow_requires_both_metrics_to_expand(self):
        data = pd.DataFrame(
            {
                "vol": [100] * 30 + [130] * 7,
                "turnover_rate": [1] * 30 + [1.3] * 7,
                "pct_chg": [0] * 30 + [1, 1, 1, 1, 1, -1, 1],
            }
        )

        self.assertTrue(is_funds_inflow_by_volume_turnover(data))
        data.loc[30:, "turnover_rate"] = 1.05
        self.assertFalse(is_funds_inflow_by_volume_turnover(data))

    def test_both_double_bottom_algorithms_detect_confirmed_breakout(self):
        closes = [11.0] * 35
        volumes = [1000.0] * 35
        closes[5], volumes[5] = 10.0, 1200
        closes[12] = 12.0
        closes[20], volumes[20] = 10.2, 700
        closes[21:25] = [10.5] * 4
        closes[25], volumes[25] = 11.0, 2000
        self.assertTrue(is_double_bottom(pd.DataFrame({"close": closes, "vol": volumes})))

        closes = [12.0] * 40
        volumes = [1000.0] * 40
        pattern = [12, 11.5, 11, 10.5, 10, 10.5, 11, 12, 13, 12.5, 12, 11.5, 11, 10.6, 10.2, 10.6, 11, 12, 13, 14]
        closes[4:24] = pattern
        volumes[8], volumes[18], volumes[23] = 1200, 700, 2000
        data = pd.DataFrame({"close": closes, "vol": volumes})
        self.assertTrue(is_double_bottom_new(data, window=2, min_days=5, max_days=20))

    def test_macd_cross_windows_detect_recent_reversal(self):
        closes = [10 - index * 0.1 for index in range(20)] + [8.1, 8.2, 8.5, 9, 10, 11, 12]
        data = pd.DataFrame({"close": closes})

        self.assertTrue(is_macd_golden_cross(data, short_window=2, long_window=5, signal_window=2, days=7))
        self.assertTrue(
            is_macd_golden_cross_7(
                data, short_window=2, long_window=5, signal_window=2, recent_days=7, max_price_change=0.2
            )
        )

    def test_pure_strategy_evaluator_does_not_mutate_caller_dataframe(self):
        data = pd.DataFrame(
            {
                "trade_time": pd.date_range("2026-01-01", periods=61),
                "close": [10.0] * 61,
                "pre_close": [10.0] * 61,
                "pct_chg": [0.0] * 61,
                "vol": [1000.0] * 61,
                "amount": [10000.0] * 61,
                "turnover_rate": [1.0] * 61,
                "is_st": [0] * 61,
            }
        )
        original_columns = list(data.columns)

        result = evaluate_daily_strategies(data, "600000.SH", "浦发银行")

        self.assertEqual([StockStatus.NO_MATCH], result)
        self.assertEqual(original_columns, list(data.columns))


if __name__ == "__main__":
    unittest.main()
