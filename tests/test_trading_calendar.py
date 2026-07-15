import unittest
from datetime import datetime

from etf_monitor.etf_monitor_scheduler import BAR_CLOSE_TIMES, latest_expected_bar_time
from market_data.trading_calendar import is_trading_time


class FakeCalendarService:
    def __init__(self, trading_day=True):
        self.trading_day = trading_day

    def is_trading_day(self, value):
        return self.trading_day


class TradingCalendarTest(unittest.TestCase):
    def test_excludes_pre_open_and_lunch_break(self):
        service = FakeCalendarService()
        self.assertFalse(is_trading_time(datetime(2026, 7, 14, 9, 15), service))
        self.assertTrue(is_trading_time(datetime(2026, 7, 14, 10, 0), service))
        self.assertFalse(is_trading_time(datetime(2026, 7, 14, 12, 0), service))
        self.assertTrue(is_trading_time(datetime(2026, 7, 14, 14, 0), service))

    def test_excludes_exchange_holiday(self):
        self.assertFalse(is_trading_time(datetime(2026, 7, 14, 10, 0), FakeCalendarService(False)))

    def test_jobs_are_aligned_after_bar_close(self):
        self.assertIn("09:45:10", BAR_CLOSE_TIMES["15min"])
        self.assertIn("11:30:10", BAR_CLOSE_TIMES["120min"])
        self.assertIn("15:00:10", BAR_CLOSE_TIMES["60min"])
        expected = latest_expected_bar_time("15min", datetime(2026, 7, 14, 14, 36, 0))
        self.assertEqual(datetime(2026, 7, 14, 14, 30, 0), expected)


if __name__ == "__main__":
    unittest.main()
