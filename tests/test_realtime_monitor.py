import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

from etf_monitor.ma_monitor import InsufficientSignalDataError, build_signal_snapshot
from etf_monitor.realtime_monitor import MIN_REQUEST_GAP_SECONDS, SCAN_INTERVAL_MINUTES, RealtimeMonitorManager
from market_data.database import MarketDataDatabase
from tests.test_market_data import sample_etf_list


class FakeMinuteService:
    def __init__(self, data, database=None):
        self.data = data
        self.database = database
        self.calls = []
        self.closed = False

    def get_minute_data(self, symbol, period, start_date, end_date, minimum_trade_time=None):
        self.calls.append((symbol, period, minimum_trade_time))
        result = self.data.copy()
        result.attrs["source"] = "sina"
        if self.database is not None:
            self.database.save_klines(symbol, period, result, "sina")
        return result

    def close(self):
        self.closed = True


class AliveThread:
    """模拟存活的后台线程，用于验证即时扫描入队而不真正启动线程。"""

    @staticmethod
    def is_alive():
        return True


class RealtimeMonitorTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = MarketDataDatabase(Path(self.temp_dir.name) / "market.db")
        self.database.replace_etf_list(sample_etf_list(), "seed")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_scan_intervals_are_half_of_each_requested_period(self):
        self.assertEqual({"15min": 7, "30min": 15, "60min": 30, "120min": 60}, SCAN_INTERVAL_MINUTES)

    def test_six_etfs_are_evenly_staggered_inside_each_period_window(self):
        now = datetime(2026, 7, 20, 10, 0)
        monitors = [{"symbol": f"51030{index}.SH"} for index in range(6)]
        next_runs = {}

        RealtimeMonitorManager._sync_instrument_schedules(next_runs, monitors, now)

        fifteen_minute_times = [next_runs[(monitor["symbol"], "15min")] for monitor in monitors]
        thirty_minute_times = [next_runs[(monitor["symbol"], "30min")] for monitor in monitors]
        self.assertEqual([70.0] * 5, [(right - left).total_seconds() for left, right in zip(fifteen_minute_times, fifteen_minute_times[1:])])
        self.assertEqual([150.0] * 5, [(right - left).total_seconds() for left, right in zip(thirty_minute_times, thirty_minute_times[1:])])

    def test_due_jobs_observe_global_request_gap(self):
        now = datetime(2026, 7, 20, 10, 0)
        next_runs = {("510300.SH", "15min"): now, ("159915.SZ", "15min"): now}

        first_job = RealtimeMonitorManager._next_due_job(next_runs, set(), now, None)
        blocked_job = RealtimeMonitorManager._next_due_job(next_runs, set(), now, now)
        next_job = RealtimeMonitorManager._next_due_job(
            next_runs,
            set(),
            now.replace(second=MIN_REQUEST_GAP_SECONDS),
            now,
        )

        self.assertIsNotNone(first_job)
        self.assertIsNone(blocked_job)
        self.assertIsNotNone(next_job)

    def test_running_manager_queues_and_deduplicates_immediate_scan_symbols(self):
        manager = RealtimeMonitorManager(self.database)
        manager._thread = AliveThread()
        manager._status["status"] = "running"

        self.assertTrue(manager.request_scan("510300.sh"))
        self.assertTrue(manager.request_scan("510300.SH"))
        self.assertEqual(("510300.SH",), manager._take_pending_symbols())
        self.assertEqual((), manager._take_pending_symbols())

    def test_stopped_manager_does_not_queue_immediate_scan(self):
        manager = RealtimeMonitorManager(self.database)

        self.assertFalse(manager.request_scan("510300.SH"))
        self.assertEqual((), manager._take_pending_symbols())

    def test_stopped_manager_initializes_missing_periods_in_background(self):
        self.database.add_realtime_monitor("510300.SH", "etf")
        now = datetime(2026, 7, 19, 12, 0)
        trading_days = pd.bdate_range(end="2026-07-17", periods=35)
        trade_times = [
            pd.Timestamp(f"{trading_day:%Y-%m-%d} {close_time}")
            for trading_day in trading_days
            for close_time in ("11:30", "15:00")
        ]
        data = pd.DataFrame(
            {
                "trade_time": trade_times,
                "close": [4.0 + index * 0.001 for index in range(70)],
            }
        )
        services = []

        def service_factory(database):
            service = FakeMinuteService(data, database)
            services.append(service)
            return service

        manager = RealtimeMonitorManager(
            self.database,
            service_factory=service_factory,
            now_provider=lambda: now,
            request_gap_seconds=0,
        )

        self.assertTrue(manager.request_initialization("510300.sh"))
        initialization_thread = manager._initialization_thread
        if initialization_thread:
            initialization_thread.join(timeout=2)

        self.assertEqual(list(("15min", "30min", "60min", "120min")), [call[1] for call in services[0].calls])
        self.assertTrue(all(len(self.database.load_klines("510300.SH", period)) >= 61 for period in SCAN_INTERVAL_MINUTES))
        self.assertEqual(4, len(self.database.get_realtime_signal_states(["510300.SH"])))
        self.assertEqual([], manager.get_status()["initializing_symbols"])
        self.assertTrue(services[0].closed)

    def test_initialization_is_skipped_when_all_period_caches_are_sufficient(self):
        self.database.add_realtime_monitor("510300.SH", "etf")
        data = pd.DataFrame(
            {
                "trade_time": pd.date_range(end="2026-07-17 15:00", periods=61, freq="15min"),
                "close": [4.0] * 61,
            }
        )
        for period in SCAN_INTERVAL_MINUTES:
            self.database.save_klines("510300.SH", period, data, "sina")
        manager = RealtimeMonitorManager(self.database)

        self.assertFalse(manager.request_initialization("510300.SH"))

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

    def test_immediate_period_scan_only_processes_requested_etf(self):
        self.database.add_realtime_monitor("510300.SH", "etf")
        self.database.add_realtime_monitor("159915.SZ", "etf")
        now = datetime(2026, 7, 17, 10, 46)
        data = pd.DataFrame(
            {
                "trade_time": pd.date_range(end="2026-07-17 11:00", periods=66, freq="15min"),
                "close": [4.0 + index * 0.001 for index in range(66)],
            }
        )
        service = FakeMinuteService(data)
        manager = RealtimeMonitorManager(self.database, now_provider=lambda: now)

        manager._scan_period(service, "15min", now, symbols=("159915.SZ",))

        self.assertEqual(["159915.SZ"], [call[0] for call in service.calls])

    def test_priority_initialization_loads_period_without_current_completed_bar(self):
        self.database.add_realtime_monitor("510300.SH", "etf")
        now = datetime(2026, 7, 17, 10, 46)
        data = pd.DataFrame(
            {
                "trade_time": pd.date_range(end="2026-07-16 15:00", periods=66, freq="120min"),
                "close": [4.0 + index * 0.001 for index in range(66)],
            }
        )
        service = FakeMinuteService(data)
        manager = RealtimeMonitorManager(self.database, now_provider=lambda: now)

        manager._scan_period(service, "120min", now, symbols=("510300.SH",), allow_historical=True)

        self.assertEqual(1, len(service.calls))
        self.assertIsNone(service.calls[0][2])
        self.assertEqual(1, len(self.database.get_realtime_signal_states(["510300.SH"])))
