import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

from etf_monitor.ma_monitor import InsufficientSignalDataError, build_signal_snapshot
from etf_monitor.realtime_monitor import SCAN_INTERVAL_MINUTES, RealtimeMonitorManager
from market_data.database import MarketDataDatabase
from tests.test_market_data import sample_etf_list


class FakeMinuteService:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def get_minute_data(self, symbol, period, start_date, end_date, minimum_trade_time=None):
        self.calls.append((symbol, period, minimum_trade_time))
        result = self.data.copy()
        result.attrs["source"] = "sina"
        return result


class RealtimeMonitorTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = MarketDataDatabase(Path(self.temp_dir.name) / "market.db")
        self.database.replace_etf_list(sample_etf_list(), "seed")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_scan_intervals_are_half_of_each_requested_period(self):
        self.assertEqual({"15min": 7, "30min": 15, "60min": 30, "120min": 60}, SCAN_INTERVAL_MINUTES)

    def test_signal_snapshot_detects_cross_and_three_ma_position(self):
        data = pd.DataFrame(
            {
                "trade_time": pd.date_range("2026-07-16 09:45", periods=61, freq="15min"),
                "close": [10.0] * 60 + [11.0],
            }
        )

        snapshot = build_signal_snapshot(data)

        self.assertEqual({10: "up", 30: "up", 60: "up"}, snapshot["crosses"])
        self.assertTrue(snapshot["above_all"])

    def test_signal_snapshot_detects_down_cross_and_equal_price_is_not_above(self):
        down = pd.DataFrame(
            {
                "trade_time": pd.date_range("2026-07-16 09:45", periods=61, freq="15min"),
                "close": [10.0] * 60 + [9.0],
            }
        )
        equal = down.copy()
        equal["close"] = 10.0

        down_snapshot = build_signal_snapshot(down)
        equal_snapshot = build_signal_snapshot(equal)

        self.assertEqual({10: "down", 30: "down", 60: "down"}, down_snapshot["crosses"])
        self.assertFalse(down_snapshot["above_all"])
        self.assertEqual({10: None, 30: None, 60: None}, equal_snapshot["crosses"])
        self.assertFalse(equal_snapshot["above_all"])

    def test_signal_snapshot_requires_previous_and_current_ma60(self):
        data = pd.DataFrame(
            {
                "trade_time": pd.date_range("2026-07-16 09:45", periods=60, freq="15min"),
                "close": [10.0] * 60,
            }
        )

        with self.assertRaisesRegex(InsufficientSignalDataError, "至少需要61根完整K线"):
            build_signal_snapshot(data)

    def test_period_scan_uses_expected_completed_bar_and_persists_state(self):
        self.database.add_realtime_monitor("510300.SH", "etf")
        now = datetime(2026, 7, 17, 10, 46)
        data = pd.DataFrame(
            {
                "trade_time": pd.date_range(end="2026-07-17 11:00", periods=66, freq="15min"),
                "close": [4.0 + index * 0.001 for index in range(66)],
            }
        )
        service = FakeMinuteService(data)
        manager = RealtimeMonitorManager(self.database, now_provider=lambda: now)
        self.database.save_klines("510300.SH", "15min", data, "sina")

        manager._scan_period(service, "15min", now)

        states = self.database.get_realtime_signal_states(["510300.SH"])
        cached = self.database.load_klines("510300.SH", "15min")
        self.assertEqual(1, len(states))
        self.assertEqual("2026-07-17 10:45:00", states[0]["bar_time"])
        self.assertEqual("sina", states[0]["source"])
        self.assertEqual("15min", service.calls[0][1])
        self.assertAlmostEqual(float(data.iloc[:-1]["close"].tail(60).mean()), float(states[0]["ma60"]))
        self.assertAlmostEqual(float(data["close"].tail(60).mean()), float(cached.iloc[-1]["ma60"]))

    def test_period_scan_persists_insufficient_sample_as_distinct_issue(self):
        self.database.add_realtime_monitor("510300.SH", "etf")
        now = datetime(2026, 7, 17, 10, 46)
        data = pd.DataFrame(
            {
                "trade_time": pd.date_range(end="2026-07-17 11:00", periods=61, freq="15min"),
                "close": [4.0 + index * 0.001 for index in range(61)],
            }
        )
        service = FakeMinuteService(data)
        manager = RealtimeMonitorManager(self.database, now_provider=lambda: now)

        manager._scan_period(service, "15min", now)

        state = self.database.get_realtime_signal_states(["510300.SH"])[0]
        self.assertTrue(state["error_message"].startswith("样本不足："))
        self.assertIsNone(state["cross_ma60"])
