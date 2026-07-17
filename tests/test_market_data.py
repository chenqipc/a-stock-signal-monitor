import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import requests

import market_data.service as service_module
from market_data.database import MarketDataDatabase
from market_data.providers.baostock_provider import BaoStockProvider
from market_data.providers.eastmoney_provider import EastMoneyProvider
from market_data.service import MarketDataService
from stock_signal_monitor.scan_market import can_compute_from_database
from stock_signal_monitor.scan_market import retry_scan_errors
from stock_signal_monitor.scan_market import scan_market
from stock_signal_monitor.stock_strategy import daily_strategy_window
from stock_signal_monitor.stock_status import StockStatus


class FakeProvider:
    supported_periods = {"D", "15min"}

    def __init__(self, name, data=None, error=None, stock_data=None, etf_data=None):
        self.name = name
        self.data = data
        self.error = error
        self.stock_data = stock_data
        self.etf_data = etf_data
        self.calls = 0
        self.requests = []

    def fetch_bars(self, symbol, period, start_date, end_date):
        self.calls += 1
        self.requests.append((symbol, period, pd.Timestamp(start_date), pd.Timestamp(end_date)))
        if self.error:
            raise self.error
        return self.data.copy()

    def fetch_stock_list(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.stock_data.copy()

    def fetch_etf_list(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.etf_data.copy() if self.etf_data is not None else pd.DataFrame()

    def close(self):
        return None


def sample_bars():
    return pd.DataFrame(
        {
            "trade_time": pd.to_datetime(["2026-07-10", "2026-07-13"]),
            "open": [10.0, 10.1],
            "close": [10.1, 10.2],
            "high": [10.2, 10.3],
            "low": [9.9, 10.0],
            "vol": [1000, 1200],
            "amount": [10000, 12200],
            "pre_close": [9.9, 10.1],
            "pct_chg": [2.02, 0.99],
            "turnover_rate": [1.0, 1.2],
            "is_st": [0, 0],
        }
    )


def sample_stock_list():
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "600000.SH"],
            "symbol": ["000001", "600000"],
            "name": ["平安银行", "浦发银行"],
            "market": ["深圳", "上海"],
            "list_date": ["19910403", "19991110"],
        }
    )


def sample_etf_list():
    return pd.DataFrame(
        {
            "ts_code": ["510300.SH", "159915.SZ"],
            "symbol": ["510300", "159915"],
            "name": ["沪深300ETF", "创业板ETF"],
            "market": ["上海", "深圳"],
        }
    )


def sample_bars_through_july_15(revise_history=False):
    history = sample_bars()
    if revise_history:
        history[["open", "close", "high", "low", "pre_close"]] *= 0.95
    recent = pd.DataFrame(
        {
            "trade_time": pd.to_datetime(["2026-07-14", "2026-07-15"]),
            "open": [10.2, 10.3],
            "close": [10.3, 10.4],
            "high": [10.4, 10.5],
            "low": [10.1, 10.2],
            "vol": [1300, 1400],
            "amount": [13390, 14560],
            "pre_close": [10.2, 10.3],
            "pct_chg": [0.98, 0.97],
            "turnover_rate": [1.3, 1.4],
            "is_st": [0, 0],
        }
    )
    return pd.concat([history, recent], ignore_index=True)


class MarketDataServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = MarketDataDatabase(Path(self.temp_dir.name) / "market.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_falls_back_to_second_provider_and_persists_result(self):
        broken = FakeProvider("broken", error=RuntimeError("offline"))
        healthy = FakeProvider("healthy", sample_bars())
        service = MarketDataService(self.database, [broken, healthy], [broken, healthy], max_retries=1)

        result = service.get_daily_data("000001.SZ", "2026-07-01", "2026-07-14", force_refresh=True)

        self.assertEqual("healthy", result.attrs["source"])
        self.assertEqual(1, broken.calls)
        self.assertEqual(1, healthy.calls)
        cached = self.database.load_klines("000001.SZ", "D", "2026-07-01", "2026-07-14")
        self.assertEqual(2, len(cached))

    def test_skips_provider_that_does_not_support_symbol_market(self):
        unsupported = FakeProvider("unsupported", error=AssertionError("unsupported provider must not be called"))
        unsupported.supports_symbol = lambda _symbol: False
        healthy = FakeProvider("healthy", sample_bars())
        service = MarketDataService(self.database, [unsupported, healthy], [unsupported, healthy], max_retries=1)

        result = service.get_daily_data("HSI.HK", "2026-07-01", "2026-07-14", force_refresh=True)

        self.assertEqual("healthy", result.attrs["source"])
        self.assertEqual(0, unsupported.calls)
        self.assertEqual(1, healthy.calls)

    def test_uses_stale_sqlite_cache_when_all_network_sources_fail(self):
        self.database.save_klines("000001.SZ", "D", sample_bars(), "seed")
        broken = FakeProvider("broken", error=RuntimeError("offline"))
        service = MarketDataService(self.database, [broken], [broken], max_retries=1)

        result = service.get_daily_data("000001.SZ", "2026-07-01", "2026-07-14", force_refresh=True)

        self.assertEqual("sqlite_stale_cache", result.attrs["source"])
        self.assertEqual(2, len(result))

    def test_completed_daily_range_uses_sqlite_without_ttl(self):
        self.database.save_klines(
            "000001.SZ", "D", sample_bars(), "seed", coverage_start="2026-07-01", coverage_end="2026-07-13"
        )
        provider = FakeProvider("network", sample_bars())
        service = MarketDataService(
            self.database, [provider], [provider], max_retries=1, now_provider=lambda: datetime(2026, 7, 15, 16, 0)
        )

        result = service.get_daily_data("000001.SZ", "2026-07-01", "2026-07-13")

        self.assertEqual("sqlite_cache", result.attrs["source"])
        self.assertEqual(0, provider.calls)

    def test_daily_cache_missing_volume_is_refetched_and_persisted(self):
        incomplete = sample_bars()
        incomplete["vol"] = None
        self.database.save_klines(
            "000001.SZ", "D", incomplete, "seed", coverage_start="2026-07-01", coverage_end="2026-07-13"
        )
        provider = FakeProvider("network", sample_bars())
        service = MarketDataService(
            self.database, [provider], [provider], max_retries=1, now_provider=lambda: datetime(2026, 7, 15, 16, 0)
        )

        result = service.get_daily_data("000001.SZ", "2026-07-01", "2026-07-13")

        self.assertEqual("network", result.attrs["source"])
        self.assertEqual(1, provider.calls)
        self.assertTrue(self.database.load_klines("000001.SZ", "D")["vol"].notna().all())

    def test_daily_cache_fetches_only_recent_overlap_for_missing_day(self):
        self.database.save_klines(
            "000001.SZ", "D", sample_bars(), "seed", coverage_start="2026-07-01", coverage_end="2026-07-13"
        )
        provider = FakeProvider("network", sample_bars_through_july_15())
        service = MarketDataService(
            self.database, [provider], [provider], max_retries=1, now_provider=lambda: datetime(2026, 7, 15, 16, 0)
        )

        refreshed = service.get_daily_data("000001.SZ", "2026-07-01", "2026-07-15")
        cached = service.get_daily_data("000001.SZ", "2026-07-01", "2026-07-15")

        self.assertEqual("network", refreshed.attrs["source"])
        self.assertEqual("sqlite_cache", cached.attrs["source"])
        self.assertEqual(1, provider.calls)
        self.assertEqual(pd.Timestamp("2026-07-10"), provider.requests[0][2])
        self.assertEqual(pd.Timestamp("2026-07-15"), provider.requests[0][3])

    def test_intraday_daily_refresh_requires_and_persists_current_bar(self):
        self.database.save_klines(
            "000001.SH", "D", sample_bars(), "seed", coverage_start="2026-07-01", coverage_end="2026-07-13"
        )
        provider = FakeProvider("network", sample_bars_through_july_15())
        service = MarketDataService(
            self.database, [provider], [provider], max_retries=1, now_provider=lambda: datetime(2026, 7, 15, 10, 0)
        )

        result = service.get_daily_data(
            "000001.SH", "2026-07-01", "2026-07-15", minimum_trade_time="2026-07-15", refresh_latest=True
        )

        self.assertEqual("network", result.attrs["source"])
        self.assertEqual(pd.Timestamp("2026-07-15"), result["trade_time"].max())
        self.assertEqual(pd.Timestamp("2026-07-15"), provider.requests[0][3])

    def test_daily_cache_fills_missing_history_before_using_incremental_mode(self):
        self.database.save_klines("000001.SZ", "D", sample_bars(), "seed")
        provider = FakeProvider("network", sample_bars_through_july_15())
        service = MarketDataService(
            self.database, [provider], [provider], max_retries=1, now_provider=lambda: datetime(2026, 7, 15, 16, 0)
        )

        service.get_daily_data("000001.SZ", "2026-07-01", "2026-07-15")

        self.assertEqual(1, provider.calls)
        self.assertEqual(pd.Timestamp("2026-07-01"), provider.requests[0][2])

    def test_adjusted_price_change_triggers_full_window_refresh(self):
        self.database.save_klines(
            "000001.SZ", "D", sample_bars(), "seed", coverage_start="2026-07-01", coverage_end="2026-07-13"
        )
        provider = FakeProvider("network", sample_bars_through_july_15(revise_history=True))
        service = MarketDataService(
            self.database, [provider], [provider], max_retries=1, now_provider=lambda: datetime(2026, 7, 15, 16, 0)
        )

        service.get_daily_data("000001.SZ", "2026-07-01", "2026-07-15")

        self.assertEqual(2, provider.calls)
        self.assertEqual(pd.Timestamp("2026-07-10"), provider.requests[0][2])
        self.assertEqual(pd.Timestamp("2026-07-01"), provider.requests[1][2])

    def test_cached_scan_does_not_call_network_provider(self):
        start_date, end_date = daily_strategy_window()
        self.database.replace_stock_list(sample_stock_list(), "seed")
        self.database.replace_etf_list(sample_etf_list(), "seed")
        for symbol in ("000001.SZ", "600000.SH", "510300.SH", "159915.SZ"):
            self.database.save_klines(symbol, "D", sample_bars(), "seed", coverage_start=start_date, coverage_end=end_date)
        provider = FakeProvider("network", error=RuntimeError("offline"))
        service = MarketDataService(self.database, [provider], [provider], max_retries=1, minimum_stock_count=1)

        signal_dir = Path(self.temp_dir.name) / "signals"
        with patch("stock_signal_monitor.scan_market.RESOURCE_DIR", signal_dir), \
                patch("stock_signal_monitor.scan_market.evaluate_daily_strategies", return_value=[StockStatus.NO_MATCH]):
            result = scan_market(service)

        self.assertEqual(4, result["processed_stocks"])
        self.assertEqual(0, provider.calls)

    def test_st_star_market_and_beijing_rows_do_not_skip_daily_cache_preparation(self):
        service = Mock()
        service.daily_cache_ready.return_value = False
        rows = (
            {"ts_code": "600001.SH", "name": "*ST测试"},
            {"ts_code": "688001.SH", "name": "科创测试"},
            {"ts_code": "830001.BJ", "name": "北交测试"},
        )

        for row in rows:
            self.assertFalse(can_compute_from_database(row, service, "20260101", "20260717"))

        self.assertEqual(3, service.daily_cache_ready.call_count)

    def test_etf_scan_only_processes_etf_and_persists_scope(self):
        start_date, end_date = daily_strategy_window()
        self.database.replace_stock_list(sample_stock_list(), "seed")
        self.database.replace_etf_list(sample_etf_list(), "seed")
        for symbol in ("510300.SH", "159915.SZ"):
            self.database.save_klines(symbol, "D", sample_bars(), "seed", coverage_start=start_date, coverage_end=end_date)
        provider = FakeProvider("network", error=RuntimeError("offline"))
        service = MarketDataService(self.database, [provider], [provider], max_retries=1, minimum_stock_count=1)

        signal_dir = Path(self.temp_dir.name) / "etf-signals"
        with patch("stock_signal_monitor.scan_market.RESOURCE_DIR", signal_dir), \
                patch("stock_signal_monitor.scan_market.evaluate_daily_strategies", return_value=[StockStatus.NO_MATCH]):
            result = scan_market(service, asset_type="etf")

        self.assertEqual(2, result["processed_stocks"])
        self.assertEqual("etf", result["scan_scope"])
        self.assertEqual("etf", self.database.get_scan_run(result["run_id"])["scan_scope"])
        self.assertEqual(0, provider.calls)

    @patch("time.sleep")
    def test_network_retry_uses_exponential_backoff(self, sleep_mock):
        provider = FakeProvider("broken", error=RuntimeError("offline"))
        service = MarketDataService(self.database, [provider], [provider], max_retries=8)

        with self.assertRaises(Exception):
            service.get_daily_data("000001.SZ", "2026-07-01", "2026-07-14", force_refresh=True)

        self.assertEqual([1, 2, 4, 8, 16, 32, 60], [call.args[0] for call in sleep_mock.call_args_list])

    def test_notification_state_is_persistent_and_idempotent(self):
        first = self.database.should_send_notification("000001:15min:MA10:up", "2026-07-14 10:00", "up")
        second = self.database.should_send_notification("000001:15min:MA10:up", "2026-07-14 10:00", "up")
        next_bar = self.database.should_send_notification("000001:15min:MA10:up", "2026-07-14 10:15", "up")

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(next_bar)

    def test_prune_minute_klines_keeps_daily_history(self):
        daily = sample_bars()
        minute = pd.DataFrame(
            {
                "trade_time": pd.date_range("2026-05-07 10:00", periods=70, freq="D"),
                "close": [10.0] * 70,
            }
        )
        self.database.save_klines("000001.SZ", "D", daily, "seed")
        self.database.save_klines("000001.SZ", "15min", minute, "seed")

        deleted = self.database.prune_minute_klines(2, now="2026-07-16")

        self.assertEqual(8, deleted)
        self.assertEqual(2, len(self.database.load_klines("000001.SZ", "D")))
        self.assertEqual(62, len(self.database.load_klines("000001.SZ", "15min")))

    def test_kline_moving_averages_survive_raw_market_data_upsert(self):
        minute = pd.DataFrame(
            {
                "trade_time": pd.date_range("2026-07-01 09:45", periods=65, freq="15min"),
                "close": [4.0 + index * 0.01 for index in range(65)],
            }
        )
        prepared = minute.copy()
        for window in (10, 30, 60):
            prepared[f"MA{window}"] = prepared["close"].rolling(window).mean()
        self.database.save_klines("510300.SH", "15min", minute, "sina")
        self.database.save_kline_moving_averages("510300.SH", "15min", prepared)
        self.database.save_klines("510300.SH", "15min", minute.tail(2), "eastmoney")

        cached = self.database.load_klines("510300.SH", "15min")

        self.assertEqual(65, len(cached))
        self.assertAlmostEqual(float(prepared.iloc[-1]["MA60"]), float(cached.iloc[-1]["ma60"]))

    def test_prune_removes_legacy_partial_120min_bars(self):
        bars = pd.DataFrame(
            {
                "trade_time": pd.to_datetime(
                    ["2026-07-17 10:30", "2026-07-17 11:30", "2026-07-17 14:00", "2026-07-17 15:00"]
                ),
                "close": [4.0, 4.1, 4.2, 4.3],
            }
        )
        self.database.save_klines("510300.SH", "120min", bars, "legacy")

        deleted = self.database.prune_minute_klines(365)
        cached = self.database.load_klines("510300.SH", "120min")

        self.assertEqual(2, deleted)
        self.assertEqual(["11:30", "15:00"], cached["trade_time"].dt.strftime("%H:%M").tolist())

    def test_close_default_service_clears_singleton_before_closing(self):
        service = Mock()
        with patch.object(service_module, "_default_service", service):
            service_module.close_default_service()
            self.assertIsNone(service_module._default_service)

        service.close.assert_called_once_with()

    def test_compact_date_format_can_read_sqlite_cache(self):
        self.database.save_klines("000001.SZ", "D", sample_bars(), "seed")

        cached = self.database.load_klines("000001.SZ", "D", "20260701", "20260714")

        self.assertEqual(2, len(cached))

    def test_stale_intraday_source_falls_back_to_fresh_source(self):
        today = datetime.now().date()
        stale_data = sample_bars()
        stale_data["trade_time"] = pd.to_datetime([f"{today} 09:30", f"{today} 09:45"])
        fresh_data = sample_bars()
        fresh_data["trade_time"] = pd.to_datetime([f"{today} 09:45", f"{today} 10:00"])
        stale = FakeProvider("stale", stale_data)
        fresh = FakeProvider("fresh", fresh_data)
        service = MarketDataService(self.database, [stale, fresh], [stale, fresh], max_retries=1)

        result = service.get_minute_data(
            "000001.SZ", "15min", str(today), str(today), force_refresh=True, minimum_trade_time=f"{today} 10:00"
        )

        self.assertEqual("fresh", result.attrs["source"])
        self.assertEqual(1, stale.calls)
        self.assertEqual(1, fresh.calls)

    def test_stock_list_is_persisted_and_loaded_from_sqlite(self):
        provider = FakeProvider("stock-source", stock_data=sample_stock_list())
        service = MarketDataService(self.database, [provider], [provider], max_retries=1, minimum_stock_count=1)

        refreshed = service.get_stock_list(force_refresh=True)
        cached = service.get_stock_list()

        self.assertEqual("stock-source", refreshed.attrs["source"])
        self.assertEqual("sqlite_cache", cached.attrs["source"])
        self.assertEqual(2, len(cached))
        self.assertEqual(1, provider.calls)

    def test_stock_list_refresh_failure_keeps_previous_snapshot(self):
        self.database.replace_stock_list(sample_stock_list(), "seed")
        broken = FakeProvider("broken", error=RuntimeError("offline"))
        service = MarketDataService(self.database, [broken], [broken], max_retries=1)

        result = service.get_stock_list(force_refresh=True)

        self.assertEqual("sqlite_stale_cache", result.attrs["source"])
        self.assertEqual(2, len(result))

    def test_etf_list_is_persisted_and_loaded_from_sqlite(self):
        provider = FakeProvider("etf-source", etf_data=sample_etf_list())
        service = MarketDataService(self.database, [provider], [provider], max_retries=1, minimum_etf_count=1)

        refreshed = service.get_etf_list(force_refresh=True)
        cached = service.get_etf_list()

        self.assertEqual("etf-source", refreshed.attrs["source"])
        self.assertEqual("sqlite_cache", cached.attrs["source"])
        self.assertEqual(2, len(cached))
        self.assertEqual(1, provider.calls)

    def test_eastmoney_etf_list_is_normalized(self):
        provider = EastMoneyProvider()
        payload = {
            "data": {
                "diff": [
                    {"f12": "510300", "f13": 1, "f14": "沪深300ETF"},
                    {"f12": "159915", "f13": 0, "f14": "创业板ETF"},
                ]
            }
        }
        try:
            with patch.object(provider, "_get_json", return_value=payload):
                data = provider.fetch_etf_list()
        finally:
            provider.close()

        self.assertEqual(["510300.SH", "159915.SZ"], data["ts_code"].tolist())
        self.assertEqual(["上海", "深圳"], data["market"].tolist())

    def test_eastmoney_proxy_failure_falls_back_to_direct_connection(self):
        provider = EastMoneyProvider()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": {"klines": []}}
        try:
            with patch.object(provider.session, "get", side_effect=requests.exceptions.ProxyError("proxy unavailable")):
                with patch.object(provider.direct_session, "get", return_value=response) as direct_get:
                    payload = provider._get_json(["https://example.test/data"], {}, "测试")
        finally:
            provider.close()

        self.assertEqual({"data": {"klines": []}}, payload)
        direct_get.assert_called_once()

    def test_eastmoney_uses_unadjusted_minutes_and_forward_adjusted_daily_data(self):
        provider = EastMoneyProvider()
        payload = {"data": {"klines": []}}
        try:
            with patch.object(provider, "_get_json", return_value=payload) as request:
                provider._request_klines("510300", "1", "15min", "20260701", "20260717")
                minute_params = request.call_args.args[1]
                provider._request_klines("510300", "1", "D", "20260701", "20260717")
                daily_params = request.call_args.args[1]
        finally:
            provider.close()

        self.assertEqual("0", minute_params["fqt"])
        self.assertEqual("1", daily_params["fqt"])

    def test_default_realtime_sources_exclude_baostock(self):
        service = MarketDataService(self.database)
        try:
            self.assertEqual(["sina", "eastmoney"], [provider.name for provider in service.minute_providers])
            self.assertIn("baostock", [provider.name for provider in service.daily_providers])
        finally:
            service.close()

    def test_realtime_monitor_state_and_cross_event_are_persisted(self):
        self.database.replace_etf_list(sample_etf_list(), "seed")
        monitor = self.database.add_realtime_monitor("510300.SH", "etf")
        snapshot = {
            "bar_time": "2026-07-17 10:45:00",
            "close": 4.25,
            "ma_values": {10: 4.20, 30: 4.10, 60: 4.00},
            "crosses": {10: "up", 30: None, 60: None},
            "above_all": True,
        }

        self.database.save_realtime_signal_state(monitor, "15min", snapshot, "sina")
        self.database.save_realtime_signal_state(monitor, "15min", snapshot, "sina")

        state = self.database.get_realtime_signal_states(["510300.SH"])[0]
        events = self.database.get_realtime_signal_events("510300.SH")
        self.assertEqual(1, state["above_all"])
        self.assertEqual("up", state["cross_ma10"])
        self.assertEqual("sina", state["source"])
        self.assertEqual(1, len(events))

    def test_stock_and_etf_groups_are_independent_and_support_pinning(self):
        self.database.replace_stock_list(sample_stock_list(), "seed")
        self.database.replace_etf_list(sample_etf_list(), "seed")
        stock_group = self.database.create_instrument_group("stock", "自选")
        etf_group = self.database.create_instrument_group("etf", "ETF观察")
        self.database.add_instrument_to_group(stock_group["id"], "000001.SZ")
        self.database.add_instrument_to_group(stock_group["id"], "600000.SH")
        self.database.set_group_item_pinned(stock_group["id"], "600000.SH", True)
        self.database.add_instrument_to_group(etf_group["id"], "510300.SH")

        stocks = self.database.search_stocks(group_id=stock_group["id"])
        etfs = self.database.search_etfs(group_id=etf_group["id"])
        stock_groups = self.database.list_instrument_groups("stock")
        etf_groups = self.database.list_instrument_groups("etf")

        self.assertEqual("600000.SH", stocks["items"][0]["ts_code"])
        self.assertEqual(1, stocks["items"][0]["is_pinned"])
        self.assertEqual("510300.SH", etfs["items"][0]["ts_code"])
        self.assertEqual(2, stock_groups[0]["item_count"])
        self.assertEqual(1, stock_groups[0]["pinned_count"])
        self.assertEqual(1, etf_groups[0]["item_count"])
        with self.assertRaises(ValueError):
            self.database.add_instrument_to_group(etf_group["id"], "000001.SZ")

    def test_scan_run_and_signals_are_persisted(self):
        run_id = self.database.start_scan_run(2)
        self.database.save_stock_signals(
            run_id,
            "000001.SZ",
            "平安银行",
            [StockStatus.SUPPORT_LEVEL_REBOUND.value, StockStatus.MACD_GOLDEN_CROSS.value],
        )
        self.database.finish_scan_run(run_id, "completed", 2, 1, 0)

        latest_run = self.database.get_latest_scan_run()
        summary = {item["signal_type"]: item["count"] for item in self.database.get_signal_summary()}
        signals = self.database.get_latest_signals(signal_type=StockStatus.SUPPORT_LEVEL_REBOUND.value)

        self.assertEqual("completed", latest_run["status"])
        self.assertEqual(1, latest_run["matched_stocks"])
        self.assertEqual(1, summary[StockStatus.MACD_GOLDEN_CROSS.value])
        self.assertEqual("000001.SZ", signals[0]["ts_code"])

    def test_scored_signal_details_are_persisted_as_structured_data(self):
        run_id = self.database.start_scan_run(1)
        signal_type = StockStatus.IS_UPWARD_TREND.value
        details = {
            signal_type: {
                "score": 4,
                "total_score": 5,
                "reasons": ["MA10趋势向上"],
                "metrics": {"return_20d_pct": 8.2},
            }
        }
        self.database.save_stock_signals(run_id, "000001.SZ", "平安银行", [signal_type], signal_details=details)
        self.database.finish_scan_run(run_id, "completed", 1, 1, 0)

        signal = self.database.get_latest_signals(signal_type=signal_type)[0]

        self.assertEqual(4, signal["signal_score"])
        self.assertEqual(details[signal_type], signal["signal_details"])

    def test_signals_can_be_filtered_by_stock_and_etf(self):
        run_id = self.database.start_scan_run(2)
        self.database.save_stock_signals(
            run_id, "000001.SZ", "平安银行", [StockStatus.MACD_GOLDEN_CROSS.value], asset_type="stock"
        )
        self.database.save_stock_signals(
            run_id, "510300.SH", "沪深300ETF", [StockStatus.MACD_GOLDEN_CROSS.value], asset_type="etf"
        )
        self.database.finish_scan_run(run_id, "completed", 2, 2, 0)

        stock_signals = self.database.get_latest_signals(asset_type="stock")
        etf_signals = self.database.get_latest_signals(asset_type="etf")
        etf_summary = self.database.get_signal_summary(asset_type="etf")

        self.assertEqual(["000001.SZ"], [item["ts_code"] for item in stock_signals])
        self.assertEqual(["510300.SH"], [item["ts_code"] for item in etf_signals])
        self.assertEqual(1, etf_summary[0]["count"])

    def test_scoped_scans_keep_latest_stock_and_etf_results_independent(self):
        stock_run = self.database.start_scan_run(1, scan_scope="stock")
        self.database.save_stock_signals(
            stock_run, "000001.SZ", "平安银行", [StockStatus.MACD_GOLDEN_CROSS.value], asset_type="stock"
        )
        self.database.finish_scan_run(stock_run, "completed", 1, 1, 0)
        etf_run = self.database.start_scan_run(1, scan_scope="etf")
        self.database.save_stock_signals(
            etf_run, "510300.SH", "沪深300ETF", [StockStatus.SUPPORT_LEVEL_REBOUND.value], asset_type="etf"
        )
        self.database.finish_scan_run(etf_run, "completed", 1, 1, 0)

        stock_signals = self.database.get_latest_signals(asset_type="stock")
        etf_signals = self.database.get_latest_signals(asset_type="etf")

        self.assertEqual(["000001.SZ"], [item["ts_code"] for item in stock_signals])
        self.assertEqual(["510300.SH"], [item["ts_code"] for item in etf_signals])

    def test_scan_errors_are_summarized_and_can_be_resolved(self):
        run_id = self.database.start_scan_run(2)
        self.database.save_scan_error(run_id, "000001.SZ", "平安银行", "TimeoutError", "请求超时")
        self.database.save_scan_error(run_id, "600000.SH", "浦发银行", "TimeoutError", "连接超时")
        self.database.finish_scan_run(run_id, "completed", 2, 0, 2)

        initial_summary = self.database.get_scan_error_summary(run_id)
        self.database.resolve_scan_error(run_id, "000001.SZ")
        counts = self.database.refresh_scan_run_counts(run_id)
        errors = self.database.get_scan_errors(run_id)

        self.assertEqual(2, initial_summary["unresolved"])
        self.assertEqual(1, len(initial_summary["groups"]))
        self.assertEqual("行情接口超时", initial_summary["groups"][0]["error_type"])
        self.assertEqual(2, initial_summary["groups"][0]["count"])
        self.assertEqual(1, counts["error_count"])
        self.assertEqual("resolved", next(item for item in errors if item["ts_code"] == "000001.SZ")["status"])

    def test_scan_errors_with_different_symbols_are_grouped_by_root_cause(self):
        run_id = self.database.start_scan_run(2)
        error_template = (
            "所有行情源均不可用，{symbol} D: baostock: BaoStock 查询失败: 用户未登录; "
            "eastmoney: 东方财富K线请求失败: https: ProxyError('Unable to connect to proxy')"
        )
        self.database.save_scan_error(
            run_id, "301000.SZ", "股票一", "MarketDataError", error_template.format(symbol="301000.SZ")
        )
        self.database.save_scan_error(
            run_id, "301001.SZ", "股票二", "MarketDataError", error_template.format(symbol="301001.SZ")
        )

        summary = self.database.get_scan_error_summary(run_id)
        items = self.database.get_scan_errors(run_id)

        self.assertEqual(1, len(summary["groups"]))
        self.assertEqual("多行情源连接故障", summary["groups"][0]["error_type"])
        self.assertEqual(2, summary["groups"][0]["count"])
        self.assertEqual("多行情源连接故障", items[0]["error_category"])
        self.assertIn("BaoStock 会话失效", items[0]["error_summary"])

    @patch("stock_signal_monitor.scan_market.evaluate_daily_strategies", return_value=[StockStatus.NO_MATCH])
    def test_retry_scan_errors_resolves_successful_stock(self, _evaluate_daily_strategies):
        run_id = self.database.start_scan_run(1)
        self.database.save_scan_error(run_id, "000001.SZ", "平安银行", "TimeoutError", "请求超时")
        self.database.finish_scan_run(run_id, "completed", 1, 0, 1)
        service = MarketDataService(self.database, [], [], max_retries=1)

        bars = MarketDataService._prepare_result(sample_bars())
        with patch.object(service, "get_daily_data", return_value=bars):
            result = retry_scan_errors(run_id, service)
        summary = self.database.get_scan_error_summary(run_id)
        task_run = self.database.get_task_history(1)[0]

        self.assertEqual(1, result["resolved_count"])
        self.assertEqual(0, result["error_count"])
        self.assertEqual(1, summary["resolved"])
        self.assertEqual("error_retry", task_run["task_type"])
        self.assertEqual(run_id, task_run["parent_run_id"])
        self.assertEqual("completed", task_run["status"])
        self.assertEqual(1, task_run["processed_stocks"])
        self.assertEqual(1, task_run["matched_stocks"])
        self.assertEqual(run_id, self.database.get_latest_scan_run()["id"])

    def test_running_scan_is_marked_failed_after_service_restart(self):
        run_id = self.database.start_scan_run(100)
        self.database.update_scan_run(run_id, 25, 5, 1)

        recovered_count = self.database.fail_interrupted_scan_runs()
        latest_run = self.database.get_latest_scan_run()

        self.assertEqual(1, recovered_count)
        self.assertEqual("failed", latest_run["status"])
        self.assertEqual(25, latest_run["processed_stocks"])
        self.assertIn("服务重启", latest_run["error_message"])


class FakeBaoResult:
    """模拟 BaoStock 游标结果，覆盖会话失效后的自动重登场景。"""

    def __init__(self, error_code="0", error_msg="", rows=None):
        self.error_code = error_code
        self.error_msg = error_msg
        self.fields = ["code", "code_name", "type", "status", "ipoDate"]
        self._rows = list(rows or [])
        self._index = -1

    def next(self):
        self._index += 1
        return self._index < len(self._rows)

    def get_row_data(self):
        return self._rows[self._index]


class FakeBaoClient:
    def __init__(self):
        self.query_count = 0
        self.logout_count = 0

    def query_stock_basic(self):
        self.query_count += 1
        if self.query_count == 1:
            return FakeBaoResult("1001", "用户未登录")
        return FakeBaoResult(rows=[["sz.000001", "平安银行", "1", "1", "1991-04-03"]])

    def logout(self):
        self.logout_count += 1


class BaoStockProviderTest(unittest.TestCase):
    def setUp(self):
        BaoStockProvider._shared_client = None
        BaoStockProvider._session_users = 0

    def tearDown(self):
        BaoStockProvider._shared_client = None
        BaoStockProvider._session_users = 0

    def test_rejects_hong_kong_symbol_before_query(self):
        provider = BaoStockProvider()

        self.assertFalse(provider.supports_symbol("HSI.HK"))

    def test_session_error_triggers_one_relogin_and_retry(self):
        client = FakeBaoClient()
        provider = BaoStockProvider()
        with patch.object(BaoStockProvider, "_login", side_effect=[client, client]) as login:
            data = provider.fetch_stock_list()
            provider.close()

        self.assertEqual(2, login.call_count)
        self.assertEqual(2, client.query_count)
        self.assertEqual("000001.SZ", data.iloc[0]["ts_code"])

    def test_shared_session_is_not_logged_out_while_another_provider_uses_it(self):
        client = FakeBaoClient()
        first = BaoStockProvider()
        second = BaoStockProvider()
        with patch.object(BaoStockProvider, "_login", return_value=client):
            first._ensure_login()
            second._ensure_login()
            first.close()
            self.assertEqual(0, client.logout_count)
            second.close()

        self.assertEqual(1, client.logout_count)


if __name__ == "__main__":
    unittest.main()
