import unittest

import pandas as pd

from etf_monitor.ma_monitor import detect_ma_crosses
from stock_signal_monitor.stock_strategy import (
    build_daily_signal_details,
    calculate_macd,
    calculate_upward_trend_score,
    evaluate_daily_strategies,
    get_price_limit_ratio,
    is_breakout_after_consolidation,
    is_consecutive_rise_with_amount_expansion,
    is_consecutive_rise_with_increasing_volume,
    is_double_bottom,
    is_funds_inflow_by_volume_turnover,
    is_limit_up_streak,
    is_ma10_support_rebound,
    is_macd_golden_cross,
    is_volume_breakout_ma60,
)
from stock_signal_monitor.stock_status import StockStatus, daily_strategy_statuses


class StrategyTest(unittest.TestCase):
    def test_price_limit_ratio_is_board_aware(self):
        self.assertEqual(0.10, float(get_price_limit_ratio("600000.SH")))
        self.assertEqual(0.20, float(get_price_limit_ratio("300001.SZ")))
        self.assertEqual(0.30, float(get_price_limit_ratio("830001.BJ")))
        self.assertEqual(0.05, float(get_price_limit_ratio("600000.SH", is_st=True)))

    def test_limit_up_streak_accepts_three_or_more_and_rejects_an_interruption(self):
        data = pd.DataFrame(
            {
                "pre_close": [10.00, 10.10, 11.11, 12.22, 13.44],
                "close": [10.10, 11.11, 12.22, 13.44, 14.78],
                "pct_chg": [1.0, 10.0, 10.0, 10.0, 10.0],
                "is_st": [0, 0, 0, 0, 0],
            }
        )
        self.assertTrue(is_limit_up_streak(data, "600000.SH"))
        data.loc[2, "close"] = 11.50
        self.assertFalse(is_limit_up_streak(data, "600000.SH"))

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

    def test_volume_breakout_ma60_requires_cross_volume_and_prior_below_days(self):
        closes = [10.0] * 59 + [9.8] * 5 + [10.1]
        data = pd.DataFrame({"close": closes, "vol": [1000.0] * 64 + [1300.0]})
        self.assertTrue(is_volume_breakout_ma60(data))
        data.loc[len(data) - 2, "close"] = 10.2
        self.assertFalse(is_volume_breakout_ma60(data))

    def test_ma10_support_rebound_requires_touch_rising_average_and_latest_rebound(self):
        closes = [9.0 + index * 0.1 for index in range(17)] + [10.5, 10.45, 10.7]
        lows = list(closes)
        ma10 = pd.Series(closes).rolling(10).mean()
        lows[18] = ma10.iloc[18]
        data = pd.DataFrame({"close": closes, "low": lows})

        self.assertTrue(is_ma10_support_rebound(data))
        data.loc[18, "low"] = ma10.iloc[18] * 0.95
        self.assertFalse(is_ma10_support_rebound(data))

    def test_rising_volume_and_amount_expansion_require_consistent_recent_strength(self):
        rising = pd.DataFrame({"pct_chg": [0] * 10 + [1, 1, 1], "vol": [100] * 10 + [115, 120, 125]})
        amount = pd.DataFrame(
            {
                "close": [10.0] * 11 + [10.2, 10.4, 10.6],
                "amount": [100] * 11 + [125, 130, 135],
                "pct_chg": [0] * 11 + [1, 1, 1],
            }
        )

        self.assertTrue(is_consecutive_rise_with_increasing_volume(rising))
        self.assertTrue(is_consecutive_rise_with_amount_expansion(amount))
        rising.loc[12, "vol"] = 119
        self.assertFalse(is_consecutive_rise_with_increasing_volume(rising))
        amount.loc[13, "pct_chg"] = -1
        self.assertFalse(is_consecutive_rise_with_amount_expansion(amount))

    def test_breakout_after_consolidation_requires_price_and_volume_expansion(self):
        data = pd.DataFrame({"close": [10] * 30 + [10, 10.1, 10.2, 10.3, 10.5], "vol": [100] * 30 + [130] * 5})

        self.assertTrue(is_breakout_after_consolidation(data))
        data.loc[30:, "vol"] = 105
        self.assertFalse(is_breakout_after_consolidation(data))

    def test_breakout_after_consolidation_rejects_rising_price_below_resistance(self):
        consolidation = [10.0 + (index % 5) * 0.1 for index in range(30)]
        data = pd.DataFrame(
            {
                "close": consolidation + [10.00, 10.05, 10.10, 10.15, 10.20],
                "vol": [100.0] * 30 + [130.0] * 5,
            }
        )

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

    def test_double_bottom_requires_a_recent_volume_confirmed_neckline_breakout(self):
        closes = [12.0] * 50
        volumes = [1000.0] * 50
        closes[10], volumes[10] = 10.0, 1300.0
        closes[17] = 13.0
        closes[25], volumes[25] = 10.2, 600.0
        closes[49], volumes[49] = 13.2, 2000.0
        data = pd.DataFrame({"close": closes, "vol": volumes})

        self.assertTrue(is_double_bottom(data, local_window=2))
        data.loc[49, "close"] = 12.9
        self.assertFalse(is_double_bottom(data, local_window=2))
        data.loc[40, ["close", "vol"]] = [13.2, 2000.0]
        self.assertFalse(is_double_bottom(data, local_window=2))

    def test_standard_macd_detects_recent_dif_dea_cross(self):
        closes = [10 - index * 0.1 for index in range(20)] + [8.1, 8.2, 8.5, 9, 10, 11, 12]
        data = pd.DataFrame({"close": closes})

        macd = calculate_macd(data, short_window=2, long_window=5, signal_window=2)
        self.assertTrue(pd.isna(macd["dea"].iloc[4]))
        self.assertFalse(pd.isna(macd["dea"].iloc[5]))
        self.assertTrue(is_macd_golden_cross(data, short_window=2, long_window=5, signal_window=2, recent_days=7))

    def test_upward_trend_score_matches_four_of_five_and_exposes_reasons(self):
        closes = [10.0] * 60 + [10.0 + index * 0.05 for index in range(21)]
        volumes = [100.0] * 76 + [130.0] * 5
        data = pd.DataFrame({"trade_time": pd.date_range("2026-01-01", periods=81), "close": closes, "vol": volumes})

        evaluation = calculate_upward_trend_score(data)
        details = build_daily_signal_details(data, [StockStatus.IS_UPWARD_TREND])

        self.assertTrue(evaluation["matched"])
        self.assertGreaterEqual(evaluation["score"], 4)
        self.assertEqual(evaluation["score"], details[StockStatus.IS_UPWARD_TREND.value]["score"])
        self.assertTrue(details[StockStatus.IS_UPWARD_TREND.value]["reasons"])

    def test_st_star_market_and_beijing_instruments_are_not_filtered(self):
        cases = (
            ("600001.SH", "*ST测试", [10.50, 11.03, 11.58], [10.00, 10.50, 11.03]),
            ("688001.SH", "科创测试", [12.00, 14.40, 17.28], [10.00, 12.00, 14.40]),
            ("830001.BJ", "北交测试", [13.00, 16.90, 21.97], [10.00, 13.00, 16.90]),
        )
        for ts_code, stock_name, limit_closes, pre_closes in cases:
            rows = 61
            data = pd.DataFrame(
                {
                    "trade_time": pd.date_range("2026-01-01", periods=rows),
                    "close": [10.0] * (rows - 3) + limit_closes,
                    "pre_close": [10.0] * (rows - 3) + pre_closes,
                    "pct_chg": [0.0] * (rows - 3) + [30.0] * 3,
                    "vol": [1000.0] * rows,
                    "amount": [10000.0] * rows,
                    "turnover_rate": [1.0] * rows,
                }
            )
            with self.subTest(ts_code=ts_code):
                self.assertIn(StockStatus.LIMIT_UP_STREAK, evaluate_daily_strategies(data, ts_code, stock_name))

    def test_public_indicator_catalog_contains_only_macd_and_double_bottom(self):
        self.assertEqual(
            (StockStatus.MACD_GOLDEN_CROSS, StockStatus.DOUBLE_BOTTOM),
            daily_strategy_statuses("public"),
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
