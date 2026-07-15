"""Web页面触发的单进程后台任务管理。"""

import threading
from datetime import datetime

from market_data.service import MarketDataService
from stock_signal_monitor.refresh_etf_list import refresh_etf_list
from stock_signal_monitor.refresh_stock_list import refresh_stock_list
from stock_signal_monitor.scan_market import retry_scan_errors, scan_market


class TaskManager:
    """保证刷新和扫描各自只有一个实例运行，状态仅用于实时页面反馈。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._tasks = {
            "refresh_stocks": self._empty_state("refresh_stocks"),
            "refresh_etfs": self._empty_state("refresh_etfs"),
            "scan_market": self._empty_state("scan_market"),
            "retry_errors": self._empty_state("retry_errors"),
        }

    @staticmethod
    def _empty_state(name):
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
        with self._lock:
            return {name: dict(state) for name, state in self._tasks.items()}

    def start_refresh_stocks(self):
        return self._start("refresh_stocks", self._refresh_stocks, blocked_by=("refresh_etfs", "scan_market", "retry_errors"))

    def start_refresh_etfs(self):
        return self._start("refresh_etfs", self._refresh_etfs, blocked_by=("refresh_stocks", "scan_market", "retry_errors"))

    def start_scan_market(self):
        return self._start("scan_market", scan_market, blocked_by=("refresh_stocks", "refresh_etfs", "retry_errors"))

    def start_retry_errors(self, run_id):
        target = lambda: retry_scan_errors(run_id)
        return self._start(
            "retry_errors", target, blocked_by=("refresh_stocks", "refresh_etfs", "scan_market"), context={"run_id": run_id}
        )

    def _start(self, name, target, blocked_by=(), context=None):
        with self._lock:
            for task_name in (name, *blocked_by):
                if self._tasks[task_name]["status"] == "running":
                    return False, dict(self._tasks[task_name])
            self._tasks[name] = {
                "name": name,
                "status": "running",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": None,
                "result": None,
                "error": None,
                "run_id": None,
                **(context or {}),
            }
        thread = threading.Thread(target=self._run, args=(name, target), daemon=True, name=f"web-{name}")
        thread.start()
        return True, self.get_status()[name]

    def _run(self, name, target):
        try:
            result = target()
            if hasattr(result, "attrs"):
                result = {"count": len(result), "source": result.attrs.get("source")}
            self._finish(name, "completed", result=result)
        except Exception as exc:
            self._finish(name, "failed", error=str(exc))

    def _finish(self, name, status, result=None, error=None):
        with self._lock:
            self._tasks[name].update(
                {
                    "status": status,
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "result": result,
                    "error": error,
                }
            )

    @staticmethod
    def _refresh_stocks():
        service = MarketDataService()
        try:
            return refresh_stock_list(service)
        finally:
            service.close()

    @staticmethod
    def _refresh_etfs():
        service = MarketDataService()
        try:
            return refresh_etf_list(service)
        finally:
            service.close()
