import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from market_data.adjustment import repair_cached_daily_prices
from market_data.adjustment import repair_daily_price_continuity
from market_data.adjustment import repair_minute_price_continuity
from market_data.database import MarketDataDatabase
from stock_signal_monitor.scan_market import scan_cached_row
from stock_signal_monitor.stock_status import StockStatus


def split_history():
    """模拟515050一拆三：生效日前是原始高价，生效日开始使用拆分后的新价格口径。"""
    return pd.DataFrame(
        {
            "trade_time": pd.to_datetime(["2026-05-08", "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14"]),
            "open": [3.20, 3.24, 3.30, 1.12, 1.15],
            "high": [3.27, 3.34, 3.38, 1.18, 1.16],
            "low": [3.18, 3.21, 3.28, 1.11, 1.13],
            "close": [3.24, 3.30, 3.33, 1.156, 1.14],
            "pre_close": [3.18, 3.24, 3.30, 1.11, 1.156],
            "pct_chg": [1.8868, 1.8519, 0.9091, 4.1441, -1.3841],
            "vol": [900.0, 950.0, 1000.0, 3200.0, 3100.0],
            "amount": [2916.0, 3135.0, 3330.0, 3699.2, 3534.0],
            "turnover_rate": [1.0, 1.1, 1.2, 1.3, 1.2],
            "is_st": [0, 0, 0, 0, 0],
        }
    )


class DailyAdjustmentTest(unittest.TestCase):
    def test_split_is_repaired_to_continuous_forward_adjusted_prices(self):
        repaired, events = repair_daily_price_continuity(split_history())

        self.assertEqual(1, len(events))
        self.assertAlmostEqual(1 / 3, events[0].factor, places=6)
        self.assertTrue(events[0].volume_adjusted)
        self.assertAlmostEqual(1.11, repaired.iloc[2]["close"], places=6)
        self.assertAlmostEqual(repaired.iloc[2]["close"], repaired.iloc[3]["pre_close"], places=6)
        self.assertAlmostEqual(3000.0, repaired.iloc[2]["vol"], places=5)
        self.assertEqual(3330.0, repaired.iloc[2]["amount"])

        second_pass, second_events = repair_daily_price_continuity(repaired)
        self.assertEqual([], second_events)
        pd.testing.assert_frame_equal(repaired, second_pass)

    def test_ordinary_overnight_gap_is_not_treated_as_corporate_action(self):
        data = pd.DataFrame(
            {
                "trade_time": pd.to_datetime(["2026-07-20", "2026-07-21"]),
                "open": [9.8, 11.8],
                "high": [10.1, 12.2],
                "low": [9.7, 11.7],
                "close": [10.0, 12.0],
                "pre_close": [9.8, 10.0],
                "pct_chg": [2.0408, 20.0],
                "vol": [1000.0, 3000.0],
            }
        )

        repaired, events = repair_daily_price_continuity(data)

        self.assertEqual([], events)
        pd.testing.assert_frame_equal(data, repaired)

    def test_cached_repair_persists_prices_and_invalidates_old_moving_averages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = MarketDataDatabase(Path(temp_dir) / "market.db")
            data = split_history()
            database.save_klines("515050.SH", "D", data, "baostock")
            moving_averages = data[["trade_time"]].assign(MA10=3.1, MA30=3.0, MA60=2.9)
            database.save_kline_moving_averages("515050.SH", "D", moving_averages)

            repaired, events = repair_cached_daily_prices(database, "515050.SH")
            persisted = database.load_klines("515050.SH", "D")

            self.assertEqual(1, len(events))
            self.assertAlmostEqual(1.11, persisted.iloc[2]["close"], places=6)
            self.assertAlmostEqual(3000.0, persisted.iloc[2]["vol"], places=5)
            self.assertTrue(persisted[["ma10", "ma30", "ma60"]].isna().all().all())
            self.assertAlmostEqual(repaired.iloc[2]["close"], persisted.iloc[2]["close"], places=6)
            _, repeated_events = repair_cached_daily_prices(database, "515050.SH")
            self.assertEqual([], repeated_events)

    @patch("stock_signal_monitor.scan_market.evaluate_daily_strategies", return_value=[StockStatus.NO_MATCH])
    def test_cached_scan_evaluates_repaired_prices(self, evaluate):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = MarketDataDatabase(Path(temp_dir) / "market.db")
            database.save_klines("515050.SH", "D", split_history(), "baostock")

            result = scan_cached_row(
                {"ts_code": "515050.SH", "name": "5G ETF", "asset_type": "etf"},
                database,
                pd.Timestamp("2026-05-08").date(),
                pd.Timestamp("2026-05-14").date(),
            )

            evaluated = evaluate.call_args.args[0]
            event_position = evaluated.index[evaluated["trade_time"] == pd.Timestamp("2026-05-13")][0]
            self.assertAlmostEqual(evaluated.loc[event_position - 1, "close"], evaluated.loc[event_position, "pre_close"], places=6)
            self.assertIsNone(result["error"])


class MinuteAdjustmentTest(unittest.TestCase):
    def test_one_to_four_split_repairs_minute_prices_and_volume(self):
        data = pd.DataFrame(
            {
                "trade_time": pd.to_datetime(
                    ["2026-05-21 11:30", "2026-05-21 15:00", "2026-05-22 11:30", "2026-05-22 15:00"]
                ),
                "open": [2.88, 2.844, 0.690, 0.697],
                "high": [2.904, 2.860, 0.702, 0.707],
                "low": [2.838, 2.741, 0.687, 0.694],
                "close": [2.843, 2.748, 0.697, 0.704],
                "pre_close": [2.851, 2.843, 2.748, 0.697],
                "pct_chg": [-0.28, -3.34, -74.64, 1.00],
                "vol": [46_374_400.0, 31_561_600.0, 151_826_572.0, 72_423_500.0],
                "amount": [130_000_000.0, 88_000_000.0, 105_000_000.0, 51_000_000.0],
            }
        )

        repaired, events = repair_minute_price_continuity(data)

        self.assertEqual(1, len(events))
        self.assertEqual(0.25, events[0].factor)
        self.assertAlmostEqual(0.687, repaired.iloc[1]["close"], places=6)
        self.assertAlmostEqual(126_246_400.0, repaired.iloc[1]["vol"], places=2)
        self.assertEqual(88_000_000.0, repaired.iloc[1]["amount"])
        self.assertAlmostEqual(repaired.iloc[1]["close"], repaired.iloc[2]["pre_close"], places=6)
        _, repeated_events = repair_minute_price_continuity(repaired)
        self.assertEqual([], repeated_events)

    def test_normal_overnight_limit_move_is_not_repaired(self):
        data = pd.DataFrame(
            {
                "trade_time": pd.to_datetime(["2026-07-22 15:00", "2026-07-23 11:30"]),
                "open": [10.0, 12.0],
                "close": [10.0, 11.8],
                "high": [10.1, 12.1],
                "low": [9.9, 11.7],
                "pre_close": [9.9, 10.0],
                "vol": [1000.0, 1800.0],
            }
        )

        repaired, events = repair_minute_price_continuity(data)

        self.assertEqual([], events)
        pd.testing.assert_frame_equal(data, repaired)


if __name__ == "__main__":
    unittest.main()
