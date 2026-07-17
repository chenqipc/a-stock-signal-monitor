import unittest

import pandas as pd

from market_data.periods import merge_a_share_60min_to_120min


class PeriodConversionTest(unittest.TestCase):
    @staticmethod
    def _bars(times):
        count = len(times)
        return pd.DataFrame(
            {
                "trade_time": pd.to_datetime(times),
                "open": [10.0 + index for index in range(count)],
                "close": [10.5 + index for index in range(count)],
                "high": [10.8 + index for index in range(count)],
                "low": [9.8 + index for index in range(count)],
                "vol": [100.0] * count,
                "amount": [1000.0] * count,
                "pre_close": [9.5 + index for index in range(count)],
                "turnover_rate": [1.0] * count,
                "is_st": [0] * count,
            }
        )

    def test_complete_sessions_merge_into_two_120min_bars(self):
        data = self._bars(
            ["2026-07-17 10:30", "2026-07-17 11:30", "2026-07-17 14:00", "2026-07-17 15:00"]
        )

        merged = merge_a_share_60min_to_120min(data)

        self.assertEqual(["11:30", "15:00"], merged["trade_time"].dt.strftime("%H:%M").tolist())
        self.assertEqual([200.0, 200.0], merged["vol"].tolist())
        self.assertEqual(11.5, merged.iloc[0]["close"])

    def test_missing_morning_bar_does_not_shift_afternoon_group(self):
        data = self._bars(["2026-07-17 11:30", "2026-07-17 14:00", "2026-07-17 15:00"])

        merged = merge_a_share_60min_to_120min(data)

        self.assertEqual(1, len(merged))
        self.assertEqual("15:00", merged.iloc[0]["trade_time"].strftime("%H:%M"))
        self.assertEqual(11.0, merged.iloc[0]["open"])

    def test_forming_afternoon_session_is_not_returned_as_completed_bar(self):
        data = self._bars(["2026-07-17 10:30", "2026-07-17 11:30", "2026-07-17 14:00"])

        merged = merge_a_share_60min_to_120min(data)

        self.assertEqual(1, len(merged))
        self.assertEqual("11:30", merged.iloc[0]["trade_time"].strftime("%H:%M"))


if __name__ == "__main__":
    unittest.main()
