"""Web页面触发的单进程后台任务管理。"""

import threading
from datetime import datetime

from market_data.service import MarketDataService
from stock_signal_monitor.refresh_etf_list import refresh_etf_list
from stock_signal_monitor.refresh_stock_list import refresh_stock_list
from stock_signal_monitor.scan_market import retry_scan_errors, scan_market


class TaskManager:
    """保证刷新和扫描各自只有一个实例运行，状态仅用于实时页面反馈。"""

    ACTIVE_STATUSES = {"running", "paused"}
    CONTROLLABLE_TASKS = {"scan_market", "retry_errors"}

    def __init__(self):
        self._lock = threading.Lock()
        self._controls = {}
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

    def start_scan_market(self, scan_scope="all"):
        target = lambda control=None: scan_market(control=control, asset_type=scan_scope)
        return self._start(
            "scan_market",
            target,
            blocked_by=("refresh_stocks", "refresh_etfs", "retry_errors"),
            context={"scan_scope": scan_scope},
        )

    def start_retry_errors(self, run_id):
        target = lambda control=None: retry_scan_errors(run_id, control=control)
        return self._start(
            "retry_errors", target, blocked_by=("refresh_stocks", "refresh_etfs"), context={"source_run_id": run_id}
        )

    def pause_task(self, name):
        return self._set_pause_state(name, paused=True)

    def resume_task(self, name):
        return self._set_pause_state(name, paused=False)

    def _start(self, name, target, blocked_by=(), context=None):
        with self._lock:
            for task_name in (name, *blocked_by):
                if self._tasks[task_name]["status"] in self.ACTIVE_STATUSES:
                    return False, dict(self._tasks[task_name])
            control = TaskControl(
                on_pause=lambda paused, task_name=name: self._mark_paused(task_name, paused),
                on_run_id=lambda run_id, task_name=name: self._set_run_id(task_name, run_id),
            )
            self._controls[name] = control
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
        wrapped_target = lambda: target(control=control) if name in self.CONTROLLABLE_TASKS else target()
        thread = threading.Thread(target=self._run, args=(name, wrapped_target), daemon=True, name=f"web-{name}")
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
            self._controls.pop(name, None)

    def _set_pause_state(self, name, paused):
        with self._lock:
            if name not in self.CONTROLLABLE_TASKS:
                return False, dict(self._tasks.get(name, self._empty_state(name)))
            task = self._tasks[name]
            expected = "running" if paused else "paused"
            if task["status"] != expected:
                return False, dict(task)
            control = self._controls.get(name)
            if not control:
                return False, dict(task)
            if paused:
                control.pause()
                task["status"] = "paused"
            else:
                control.resume()
                task["status"] = "running"
            return True, dict(task)

    def _mark_paused(self, name, paused):
        with self._lock:
            if self._tasks[name]["status"] in self.ACTIVE_STATUSES:
                self._tasks[name]["status"] = "paused" if paused else "running"

    def _set_run_id(self, name, run_id):
        with self._lock:
            self._tasks[name]["run_id"] = run_id

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


class TaskControl:
    """后台扫描的协作式暂停控制；不会中断正在进行中的单次网络请求。"""

    def __init__(self, on_pause=None, on_run_id=None):
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._pause_requested = threading.Event()
        self._on_pause = on_pause
        self._on_run_id = on_run_id

    def pause(self):
        self._pause_requested.set()
        self._resume_event.clear()

    def resume(self):
        self._pause_requested.clear()
        self._resume_event.set()

    def set_run_id(self, run_id):
        if self._on_run_id:
            self._on_run_id(run_id)

    def wait_if_paused(self, on_pause=None, on_resume=None):
        if not self._pause_requested.is_set():
            return
        if self._on_pause:
            self._on_pause(True)
        if on_pause:
            on_pause()
        self._resume_event.wait()
        if on_resume:
            on_resume()
        if self._on_pause:
            self._on_pause(False)
