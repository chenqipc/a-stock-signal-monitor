import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.StockEnum import StockStatus
from market_data.database import MarketDataDatabase
from tests.test_market_data import sample_bars, sample_etf_list, sample_stock_list
from web_app.app import create_app
from web_app.tasks import TaskManager


class FakeTaskManager:
    """隔离Web接口测试，避免测试期间真正启动耗时扫描。"""

    def __init__(self):
        self.tasks = {
            "refresh_stocks": self._state("refresh_stocks"),
            "refresh_etfs": self._state("refresh_etfs"),
            "scan_market": self._state("scan_market"),
            "retry_errors": self._state("retry_errors"),
        }

    @staticmethod
    def _state(name):
        return {
            "name": name,
            "status": "idle",
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
            "run_id": None,
        }

    def get_status(self):
        return self.tasks

    def start_refresh_stocks(self):
        self.tasks["refresh_stocks"]["status"] = "running"
        return True, self.tasks["refresh_stocks"]

    def start_scan_market(self, scan_scope="all"):
        self.tasks["scan_market"].update({"status": "running", "scan_scope": scan_scope})
        return True, self.tasks["scan_market"]

    def start_refresh_etfs(self):
        self.tasks["refresh_etfs"]["status"] = "running"
        return True, self.tasks["refresh_etfs"]

    def start_retry_errors(self, run_id):
        self.tasks["retry_errors"].update({"status": "running", "source_run_id": run_id})
        return True, self.tasks["retry_errors"]

    def pause_task(self, name):
        if self.tasks[name]["status"] != "running":
            return False, self.tasks[name]
        self.tasks[name]["status"] = "paused"
        return True, self.tasks[name]

    def resume_task(self, name):
        if self.tasks[name]["status"] != "paused":
            return False, self.tasks[name]
        self.tasks[name]["status"] = "running"
        return True, self.tasks[name]


class WebAppTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = MarketDataDatabase(Path(self.temp_dir.name) / "market.db")
        self.database.replace_stock_list(sample_stock_list(), "test-source")
        self.database.replace_etf_list(sample_etf_list(), "test-source")
        self.run_id = self.database.start_scan_run(2)
        self.database.save_stock_signals(self.run_id, "000001.SZ", "平安银行", [StockStatus.SUPPORT_LEVEL_REBOUND.value])
        self.database.save_scan_error(self.run_id, "600000.SH", "浦发银行", "TimeoutError", "行情接口请求超时")
        self.database.finish_scan_run(self.run_id, "completed", 2, 1, 1)
        self.settings_path = Path(self.temp_dir.name) / "app_settings.json"
        self.task_manager = FakeTaskManager()
        self.app = create_app(
            {"TESTING": True, "DATABASE_SETTINGS_PATH": self.settings_path},
            database=self.database,
            task_manager=self.task_manager,
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_index_contains_theme_and_dashboard_controls(self):
        response = self.client.get("/")
        page = response.get_data(as_text=True)
        stylesheet = Path("web_app/static/css/app.css").read_text(encoding="utf-8")
        script = Path("web_app/static/js/app.js").read_text(encoding="utf-8")

        self.assertEqual(200, response.status_code)
        self.assertIn("themeToggle", page)
        self.assertIn('id="signalTableTitle"', page)
        self.assertIn('data-action="scan-current"', page)
        self.assertIn('startTask("scan", state.signalAssetType)', script)
        self.assertNotIn("策略信号中心", page)
        self.assertNotIn("任务中心", page)
        self.assertIn('data-view-target="etfs"', page)
        self.assertIn('data-view-target="settings"', page)
        self.assertIn('data-view-target="daily-custom"', page)
        self.assertIn('data-view-target="daily-other"', page)
        self.assertIn('data-view-target="minute-custom"', page)
        self.assertIn('data-view-target="minute-other"', page)
        self.assertIn('data-nav-collapse="daily"', page)
        self.assertIn('data-nav-collapse="minute"', page)
        self.assertEqual(2, page.count('aria-expanded="false"'))
        self.assertEqual(2, page.count('class="nav-subitems" hidden'))
        self.assertIn('id="databaseSettingsForm"', page)
        self.assertIn('id="stockGroupTabs"', page)
        self.assertIn('id="etfGroupTabs"', page)
        self.assertIn('id="view-minute-custom"', page)
        self.assertIn("ETF分钟级自定义策略", page)
        self.assertIn("日线策略", page)
        self.assertIn("分钟级策略", page)
        self.assertIn('id="indexTrendChart"', page)
        self.assertIn("主要指数走势", page)
        self.assertNotIn("把分散的指标", page)
        self.assertNotIn("ETF主数据", page)
        self.assertNotIn("股票主数据", page)
        self.assertEqual(2, page.count("danger-action is-placeholder"))
        self.assertEqual(5, page.count('class="app-modal-backdrop"'))
        self.assertIn('id="confirmModal"', page)
        self.assertIn('id="dailyChartModal"', page)
        self.assertIn('id="dailyPreviewPopover"', page)
        self.assertEqual(2, page.count("data-signal-asset="))
        self.assertIn("vendor/lightweight-charts/lightweight-charts.standalone.production.js", page)
        self.assertIn("Charts by TradingView", page)
        self.assertIn("confirmAction", script)
        self.assertIn('if (viewName === "signals") viewName = "daily-custom";', script)
        self.assertIn("function toggleNavGroup", script)
        self.assertIn("function syncStrategyNav", script)
        self.assertNotIn("window.confirm", script)
        self.assertIn("vendor/tabler/tabler.min.css", page)
        self.assertIn('data-bs-theme="dark"', page)
        self.assertEqual(6, page.count('class="table table-vcenter table-hover"'))
        self.assertNotIn('class="card panel filters-panel', page)
        self.assertNotIn('class="panel filters-panel stock-filters"', page)
        self.assertIn('class="strategy-filter-grid" id="indicatorFilters"', page)
        self.assertIn('class="search-field signal-search-field"', page)
        self.assertEqual(2, page.count('class="search-field table-head-search-field"'))
        self.assertEqual(3, page.count('class="signal-table-toolbar'))
        self.assertIn("strategy-filter-card", script)
        self.assertIn("function renderProfessionalDailyChart", script)
        self.assertIn("function renderDailyPreviewCandles", script)
        self.assertIn("function drawIndexChart", script)
        self.assertNotIn("library.AreaSeries", script)
        self.assertGreaterEqual(script.count("library.CandlestickSeries"), 3)
        self.assertIn('id="indexTrendChart"', page)
        self.assertNotIn('<canvas id="indexTrendChart"', page)
        self.assertIn('class="index-ma-legend"', page)
        self.assertIn("indexMovingAverageOptions", script)
        self.assertIn("movingAverageSeries(candles, 60)", script)
        self.assertIn("subscribeVisibleLogicalRangeChange", script)
        self.assertIn("function loadOlderIndexHistory", script)
        self.assertIn("rightBarStaysOnScroll: true", script)
        self.assertIn("const INDEX_WHEEL_ZOOM_STEP = 0.08;", script)
        self.assertIn("const INDEX_WHEEL_GESTURE_DELAY = 90;", script)
        self.assertIn("mouseWheel: false", script)
        self.assertIn("function continueIndexHistoryLoading", script)
        self.assertIn("function selectSignalAssetType", script)
        self.assertIn('data-view-daily="${code}"', script)
        self.assertRegex(stylesheet, re.compile(r"\.strategy-filter-grid\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,", re.DOTALL))
        self.assertRegex(stylesheet, re.compile(r"\.filters-panel\s*\{[^}]*flex-direction:\s*row;", re.DOTALL))
        self.assertRegex(stylesheet, re.compile(r"\.modal-card\s*\{[^}]*z-index:\s*1;", re.DOTALL))
        self.assertRegex(stylesheet, re.compile(r"\.modal-shell\s*>\s*\.modal-backdrop\s*\{[^}]*z-index:\s*0;", re.DOTALL))
        self.assertRegex(stylesheet, re.compile(r"\.error-modal-body\s*\{[^}]*overflow-y:\s*auto;", re.DOTALL))
        self.assertRegex(stylesheet, re.compile(r"html\s*\{[^}]*scrollbar-gutter:\s*stable;", re.DOTALL))

    def test_dashboard_returns_dynamic_indicator_counts(self):
        payload = self.client.get("/api/dashboard").get_json()
        indicators = {item["label"]: item for item in payload["indicators"]}

        self.assertEqual(len(StockStatus) - 1, len(indicators))
        self.assertEqual(1, indicators[StockStatus.SUPPORT_LEVEL_REBOUND.value]["count"])
        self.assertEqual(2, payload["stats"]["stock_count"])
        self.assertEqual(2, payload["stats"]["etf_count"])
        self.assertEqual(1, payload["latest_scan"]["matched_stocks"])
        self.assertEqual("D", payload["indices"]["period"])

    def test_indices_endpoint_returns_major_market_trends(self):
        bars = sample_bars()
        bars.attrs["source"] = "eastmoney"

        with patch("web_app.app.MarketDataService.daily_cache_ready", return_value=False), \
                patch("web_app.app.MarketDataService.get_daily_data", return_value=bars):
            payload = self.client.get("/api/indices?limit=2&refresh=1").get_json()

        self.assertEqual("D", payload["period"])
        self.assertEqual(
            ["上证", "深成指", "创业板", "沪深300", "科创50", "恒生", "纳斯达克", "黄金现货"],
            [item["short_name"] for item in payload["items"]],
        )
        self.assertEqual(2, len(payload["items"][0]["points"]))
        self.assertEqual("eastmoney", payload["items"][0]["source"])
        self.assertFalse(payload["items"][0]["needs_refresh"])

    def test_dashboard_indices_are_loaded_from_sqlite_without_network_refresh(self):
        self.database.save_klines("000001.SH", "D", sample_bars(), "seed", coverage_start="2026-07-01", coverage_end="2026-07-13")

        with patch("web_app.app.MarketDataService.get_daily_data", side_effect=AssertionError("network should not run")):
            payload = self.client.get("/api/dashboard").get_json()

        first_index = payload["indices"]["items"][0]
        self.assertEqual("上证", first_index["short_name"])
        self.assertEqual("sqlite_cache", first_index["source"])
        self.assertEqual(2, len(first_index["points"]))
        self.assertTrue(first_index["needs_refresh"])

    def test_index_history_endpoint_returns_cached_bars_before_cursor(self):
        bars = sample_bars()
        bars["trade_time"] = bars["trade_time"] - bars["trade_time"].min() + bars["trade_time"].min().replace(month=6, day=2)
        self.database.save_klines("000001.SH", "D", bars, "seed", coverage_start="2026-06-01", coverage_end="2026-06-30")

        response = self.client.get("/api/indices/000001.SH/history?before=2026-07-01&limit=2&ensure=0")
        payload = response.get_json()

        self.assertEqual(200, response.status_code)
        self.assertEqual("000001.SH", payload["symbol"])
        self.assertEqual(2, len(payload["items"]))
        self.assertTrue(payload["has_more"])
        self.assertTrue(all(item["trade_time"] < "2026-07-01" for item in payload["items"]))

    def test_index_history_endpoint_rejects_unknown_symbol_and_invalid_date(self):
        unknown = self.client.get("/api/indices/600000.SH/history?before=2026-07-01")
        invalid = self.client.get("/api/indices/000001.SH/history?before=not-a-date")

        self.assertEqual(404, unknown.status_code)
        self.assertEqual(400, invalid.status_code)

    def test_stock_and_signal_search(self):
        stocks = self.client.get("/api/stocks?q=平安").get_json()
        signals = self.client.get(f"/api/signals?type={StockStatus.SUPPORT_LEVEL_REBOUND.value}").get_json()

        self.assertEqual(1, stocks["total"])
        self.assertEqual("000001.SZ", stocks["items"][0]["ts_code"])
        self.assertEqual(1, signals["total"])
        self.assertEqual("平安银行", signals["items"][0]["stock_name"])

    def test_signal_api_separates_stock_and_etf_results(self):
        self.database.save_stock_signals(
            self.run_id, "510300.SH", "沪深300ETF", [StockStatus.SUPPORT_LEVEL_REBOUND.value], asset_type="etf"
        )

        stocks = self.client.get("/api/signals?asset_type=stock").get_json()
        etfs = self.client.get("/api/signals?asset_type=etf").get_json()

        self.assertEqual(1, stocks["total"])
        self.assertEqual("stock", stocks["items"][0]["asset_type"])
        self.assertEqual(1, etfs["total"])
        self.assertEqual("510300.SH", etfs["items"][0]["ts_code"])
        indicator_counts = {item["label"]: item["count"] for item in etfs["indicators"]}
        self.assertEqual(1, indicator_counts[StockStatus.SUPPORT_LEVEL_REBOUND.value])

    def test_etf_list_and_independent_group_workflow(self):
        create_response = self.client.post("/api/instrument-groups", json={"asset_type": "etf", "name": "ETF自选"})
        group_id = create_response.get_json()["group"]["id"]
        add_response = self.client.post(f"/api/instrument-groups/{group_id}/items", json={"asset_code": "510300.SH"})
        pin_response = self.client.patch(
            f"/api/instrument-groups/{group_id}/items/510300.SH/pin",
            json={"pinned": True},
        )
        etfs = self.client.get(f"/api/etfs?group_id={group_id}").get_json()

        self.assertEqual(201, create_response.status_code)
        self.assertEqual(201, add_response.status_code)
        self.assertEqual(200, pin_response.status_code)
        self.assertEqual(1, etfs["total"])
        self.assertEqual("510300.SH", etfs["items"][0]["ts_code"])
        self.assertEqual(1, etfs["items"][0]["is_pinned"])
        self.assertEqual("ETF自选", etfs["groups"][0]["name"])

    def test_stock_group_item_can_be_removed_and_group_deleted(self):
        group = self.client.post("/api/instrument-groups", json={"asset_type": "stock", "name": "自选"}).get_json()["group"]
        self.client.post(f"/api/instrument-groups/{group['id']}/items", json={"asset_code": "000001.SZ"})

        removed = self.client.delete(f"/api/instrument-groups/{group['id']}/items/000001.SZ")
        deleted = self.client.delete(f"/api/instrument-groups/{group['id']}")

        self.assertEqual(200, removed.status_code)
        self.assertEqual(200, deleted.status_code)
        self.assertEqual([], self.database.list_instrument_groups("stock"))

    def test_task_endpoint_reports_started_state(self):
        response = self.client.post("/api/tasks/scan-market", json={})

        self.assertEqual(202, response.status_code)
        self.assertTrue(response.get_json()["started"])
        self.assertEqual("all", response.get_json()["task"]["scan_scope"])

    def test_strategy_tab_starts_scoped_scan_task(self):
        response = self.client.post("/api/tasks/scan-market", json={"scan_scope": "etf"})

        self.assertEqual(202, response.status_code)
        self.assertEqual("etf", response.get_json()["task"]["scan_scope"])

    def test_scan_task_rejects_unknown_scope(self):
        response = self.client.post("/api/tasks/scan-market", json={"scan_scope": "fund"})

        self.assertEqual(400, response.status_code)
        self.assertIn("扫描范围", response.get_json()["error"])

    def test_task_progress_combines_live_task_and_scan_state(self):
        payload = self.client.get("/api/task-progress?limit=1").get_json()

        self.assertIn("scan_market", payload["tasks"])
        self.assertEqual("completed", payload["latest_scan"]["status"])
        self.assertEqual(1, len(payload["task_runs"]))
        self.assertEqual(1, len(payload["scan_runs"]))
        self.assertEqual(payload["latest_scan"]["id"], payload["scan_runs"][0]["id"])

    def test_task_runs_endpoint_returns_persistent_queue(self):
        payload = self.client.get("/api/task-runs?limit=10").get_json()

        self.assertEqual(1, len(payload["items"]))
        self.assertEqual("market_scan", payload["items"][0]["task_type"])

    def test_completed_task_record_and_related_results_can_be_deleted(self):
        retry_run_id = self.database.start_retry_run(self.run_id, 1)
        self.database.finish_scan_run(retry_run_id, "failed", 1, 0, 1, "重试失败")

        response = self.client.delete(f"/api/task-runs/{self.run_id}")

        self.assertEqual(200, response.status_code)
        self.assertIsNone(self.database.get_scan_run(self.run_id))
        self.assertEqual([], self.database.get_scan_errors(self.run_id))
        self.assertEqual([], self.database.get_latest_signals())
        self.assertIsNone(self.database.get_scan_run(retry_run_id)["parent_run_id"])

    def test_running_task_record_cannot_be_deleted(self):
        running_id = self.database.start_scan_run(10)

        response = self.client.delete(f"/api/task-runs/{running_id}")

        self.assertEqual(409, response.status_code)
        self.assertIn("运行中的任务不能删除", response.get_json()["error"])
        self.assertIsNotNone(self.database.get_scan_run(running_id))

    def test_scan_task_can_be_paused_and_resumed(self):
        running_id = self.database.start_scan_run(10)
        self.task_manager.tasks["scan_market"].update({"status": "running", "run_id": running_id})

        paused = self.client.post("/api/tasks/scan_market/pause", json={})
        resumed = self.client.post("/api/tasks/scan_market/resume", json={})

        self.assertEqual(200, paused.status_code)
        self.assertEqual("paused", paused.get_json()["task"]["status"])
        self.assertEqual(200, resumed.status_code)
        self.assertEqual("running", resumed.get_json()["task"]["status"])
        self.assertEqual("running", self.database.get_scan_run(running_id)["status"])

    def test_database_settings_can_copy_and_switch_to_cloud_directory(self):
        target_directory = Path(self.temp_dir.name) / "OneDrive" / "AStockData"

        response = self.client.post(
            "/api/settings/database",
            json={"database_directory": str(target_directory), "copy_current": True, "cloud_sync_mode": True},
        )
        payload = response.get_json()

        self.assertEqual(200, response.status_code)
        self.assertTrue(payload["copied_current_database"])
        self.assertTrue(payload["cloud_sync_mode"])
        self.assertEqual("DELETE", payload["journal_mode"])
        self.assertEqual((target_directory / "market_data.db").resolve(), Path(payload["database_path"]))
        self.assertTrue((target_directory / "market_data.db").exists())
        self.assertTrue(self.settings_path.exists())
        self.assertEqual(2, self.client.get("/api/dashboard").get_json()["stats"]["stock_count"])

    def test_database_switch_is_blocked_while_task_is_running(self):
        self.task_manager.tasks["scan_market"]["status"] = "running"

        response = self.client.post(
            "/api/settings/database",
            json={"database_directory": str(Path(self.temp_dir.name) / "blocked")},
        )

        self.assertEqual(409, response.status_code)
        self.assertIn("运行中的任务", response.get_json()["error"])
        self.assertFalse(self.settings_path.exists())

    def test_scan_error_detail_and_retry_endpoint(self):
        detail = self.client.get(f"/api/scan-runs/{self.run_id}/errors").get_json()
        retry_response = self.client.post(f"/api/scan-runs/{self.run_id}/retry-errors", json={})

        self.assertEqual(1, detail["summary"]["unresolved"])
        self.assertEqual(1, len(detail["summary"]["groups"]))
        self.assertEqual("TimeoutError", detail["items"][0]["last_error_type"])
        self.assertEqual("行情接口超时", detail["items"][0]["error_category"])
        self.assertTrue(detail["can_retry"])
        self.assertEqual(202, retry_response.status_code)
        self.assertTrue(retry_response.get_json()["started"])

    def test_running_scan_errors_can_be_viewed_and_retried(self):
        running_id = self.database.start_scan_run(3)
        self.database.save_scan_error(running_id, "000001.SZ", "平安银行", "TimeoutError", "行情接口超时")
        self.database.update_scan_run(running_id, 1, 0, 1)
        self.task_manager.tasks["scan_market"]["status"] = "running"

        detail = self.client.get(f"/api/scan-runs/{running_id}/errors").get_json()
        retry_response = self.client.post(f"/api/scan-runs/{running_id}/retry-errors", json={})

        self.assertEqual(1, detail["summary"]["unresolved"])
        self.assertTrue(detail["can_retry"])
        self.assertEqual(202, retry_response.status_code)
        self.assertEqual("running", retry_response.get_json()["task"]["status"])

    def test_invalid_kline_period_returns_validation_error(self):
        response = self.client.get("/api/klines/000001.SZ?period=1min")

        self.assertEqual(400, response.status_code)

    def test_daily_chart_endpoint_fetches_and_returns_ohlcv(self):
        bars = sample_bars()
        bars.attrs["source"] = "eastmoney"

        with patch("web_app.app.MarketDataService.get_daily_data", return_value=bars) as fetch:
            response = self.client.get("/api/klines/000001.SZ?period=D&months=3&limit=90&ensure=1")

        payload = response.get_json()
        self.assertEqual(200, response.status_code)
        self.assertEqual("eastmoney", payload["source"])
        self.assertTrue(payload["has_volume"])
        self.assertTrue(payload["has_ohlcv"])
        self.assertEqual(2, len(payload["items"]))
        self.assertEqual(1000, payload["items"][0]["vol"])
        fetch.assert_called_once()

    def test_stock_refresh_is_blocked_while_market_scan_is_running(self):
        manager = TaskManager()
        manager._tasks["scan_market"]["status"] = "running"

        started, task = manager.start_refresh_stocks()

        self.assertFalse(started)
        self.assertEqual("scan_market", task["name"])

    def test_frontend_task_polling_is_visibility_gated(self):
        script = Path(__file__).resolve().parents[1].joinpath("web_app/static/js/app.js").read_text(encoding="utf-8")

        self.assertIn("function shouldPollTasks", script)
        self.assertIn('state.currentView === "runs"', script)
        self.assertIn('if (state.currentView === "runs") renderRunRows', script)
        self.assertIn("scheduleTaskPoll(1000, true)", script)
        self.assertIn('if (taskName === "scan" && data.started) showView("runs")', script)


if __name__ == "__main__":
    unittest.main()
