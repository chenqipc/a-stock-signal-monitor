import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import requests

from common.StockEnum import StockStatus
from market_data.database import MarketDataDatabase
from market_data.providers.baostock_provider import BaoStockProvider
from market_data.providers.eastmoney_provider import EastMoneyProvider
from market_data.service import MarketDataService
from stock_signal_monitor.scan_market import retry_scan_errors


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

    def test_notification_state_is_persistent_and_idempotent(self):
        first = self.database.should_send_notification("000001:15min:MA10:up", "2026-07-14 10:00", "up")
        second = self.database.should_send_notification("000001:15min:MA10:up", "2026-07-14 10:00", "up")
        next_bar = self.database.should_send_notification("000001:15min:MA10:up", "2026-07-14 10:15", "up")

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(next_bar)

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
        self.database.save_stock_signals(run_id, "000001.SZ", "平安银行", ["底部支撑反弹10日线", "最近3天MACD金叉"])
        self.database.finish_scan_run(run_id, "completed", 2, 1, 0)

        latest_run = self.database.get_latest_scan_run()
        summary = {item["signal_type"]: item["count"] for item in self.database.get_signal_summary()}
        signals = self.database.get_latest_signals(signal_type="底部支撑反弹10日线")

        self.assertEqual("completed", latest_run["status"])
        self.assertEqual(1, latest_run["matched_stocks"])
        self.assertEqual(1, summary["最近3天MACD金叉"])
        self.assertEqual("000001.SZ", signals[0]["ts_code"])

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

    @patch("stock_signal_monitor.scan_market.daily_check", return_value=[StockStatus.NO_MATCH])
    def test_retry_scan_errors_resolves_successful_stock(self, _daily_check):
        run_id = self.database.start_scan_run(1)
        self.database.save_scan_error(run_id, "000001.SZ", "平安银行", "TimeoutError", "请求超时")
        self.database.finish_scan_run(run_id, "completed", 1, 0, 1)
        service = MarketDataService(self.database, [], [], max_retries=1)

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
