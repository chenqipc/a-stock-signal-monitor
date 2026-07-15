import re
import tempfile
import unittest
from pathlib import Path

from common.StockEnum import StockStatus
from market_data.database import MarketDataDatabase
from tests.test_market_data import sample_etf_list, sample_stock_list
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
        return {"name": name, "status": "idle", "started_at": None, "finished_at": None, "result": None, "error": None}

    def get_status(self):
        return self.tasks

    def start_refresh_stocks(self):
        self.tasks["refresh_stocks"]["status"] = "running"
        return True, self.tasks["refresh_stocks"]

    def start_scan_market(self):
        self.tasks["scan_market"]["status"] = "running"
        return True, self.tasks["scan_market"]

    def start_refresh_etfs(self):
        self.tasks["refresh_etfs"]["status"] = "running"
        return True, self.tasks["refresh_etfs"]

    def start_retry_errors(self, run_id):
        self.tasks["retry_errors"].update({"status": "running", "run_id": run_id})
        return True, self.tasks["retry_errors"]


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
        self.assertNotIn("策略信号中心", page)
        self.assertNotIn("任务中心", page)
        self.assertIn('data-view-target="etfs"', page)
        self.assertIn('data-view-target="settings"', page)
        self.assertIn('id="databaseSettingsForm"', page)
        self.assertIn('id="stockGroupTabs"', page)
        self.assertIn('id="etfGroupTabs"', page)
        self.assertNotIn("ETF主数据", page)
        self.assertNotIn("股票主数据", page)
        self.assertEqual(2, page.count("danger-action is-placeholder"))
        self.assertEqual(4, page.count('class="app-modal-backdrop"'))
        self.assertIn('id="confirmModal"', page)
        self.assertIn("confirmAction", script)
        self.assertNotIn("window.confirm", script)
        self.assertIn("vendor/tabler/tabler.min.css", page)
        self.assertIn('data-bs-theme="dark"', page)
        self.assertEqual(6, page.count('class="table table-vcenter table-hover"'))
        self.assertNotIn('class="card panel filters-panel', page)
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

    def test_stock_and_signal_search(self):
        stocks = self.client.get("/api/stocks?q=平安").get_json()
        signals = self.client.get(f"/api/signals?type={StockStatus.SUPPORT_LEVEL_REBOUND.value}").get_json()

        self.assertEqual(1, stocks["total"])
        self.assertEqual("000001.SZ", stocks["items"][0]["ts_code"])
        self.assertEqual(1, signals["total"])
        self.assertEqual("平安银行", signals["items"][0]["stock_name"])

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

    def test_invalid_kline_period_returns_validation_error(self):
        response = self.client.get("/api/klines/000001.SZ?period=1min")

        self.assertEqual(400, response.status_code)

    def test_stock_refresh_is_blocked_while_market_scan_is_running(self):
        manager = TaskManager()
        manager._tasks["scan_market"]["status"] = "running"

        started, task = manager.start_refresh_stocks()

        self.assertFalse(started)
        self.assertEqual("scan_market", task["name"])


if __name__ == "__main__":
    unittest.main()
