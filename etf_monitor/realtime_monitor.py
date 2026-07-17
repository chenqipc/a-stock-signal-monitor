"""由Web页面控制的ETF实时策略监控任务。"""

import logging
import threading
from datetime import datetime, timedelta

import pandas as pd

from market_data.config import DEFAULT_MINUTE_KLINE_RETENTION_DAYS
from market_data.service import MarketDataService

from .etf_monitor_scheduler import latest_expected_bar_time
from .ma_monitor import InsufficientSignalDataError, MINIMUM_SIGNAL_BARS, build_signal_snapshot, calculate_moving_averages


logger = logging.getLogger(__name__)
MONITOR_PERIODS = ("15min", "30min", "60min", "120min")
SCAN_INTERVAL_MINUTES = {"15min": 7, "30min": 15, "60min": 30, "120min": 60}


class RealtimeMonitorManager:
    """串行拉取分钟行情并持久化信号，避免免费数据源被并发请求压垮。"""

    def __init__(
        self,
        database,
        service_factory=MarketDataService,
        now_provider=datetime.now,
        retention_days_provider=None,
    ):
        self.database = database
        self.service_factory = service_factory
        self._now = now_provider
        self._retention_days = retention_days_provider or (lambda: DEFAULT_MINUTE_KLINE_RETENTION_DAYS)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._status = {
            "status": "stopped",
            "started_at": None,
            "stopped_at": None,
            "last_scan_at": None,
            "current_period": None,
            "last_error": None,
            "next_runs": {},
        }

    def start(self):
        """启动唯一后台线程；监控池为空时拒绝启动并给出可展示原因。"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.get_status()
            if not self.database.list_realtime_monitors("etf"):
                self._status["last_error"] = "请先添加至少一只ETF到监控池"
                return self.get_status()
            now = self._now()
            self._stop_event.clear()
            self._status.update(
                {
                    "status": "running",
                    "started_at": self._format_time(now),
                    "stopped_at": None,
                    "last_error": None,
                    "next_runs": {period: self._format_time(now) for period in MONITOR_PERIODS},
                }
            )
            self._thread = threading.Thread(target=self._run, name="realtime-etf-monitor", daemon=True)
            self._thread.start()
            return self.get_status()

    def stop(self):
        """发出停止信号；当前单只ETF请求结束后线程会安全退出。"""
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                self._status["status"] = "stopped"
                return self.get_status()
            self._status["status"] = "stopping"
            self._stop_event.set()
            return self.get_status()

    def close(self):
        self.stop()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)

    def get_status(self):
        with self._lock:
            payload = dict(self._status)
            payload["next_runs"] = dict(self._status.get("next_runs") or {})
            payload["intervals"] = dict(SCAN_INTERVAL_MINUTES)
            return payload

    def _run(self):
        service = None
        next_runs = {period: self._now() for period in MONITOR_PERIODS}
        try:
            service = self.service_factory(database=self.database)
            while not self._stop_event.is_set():
                now = self._now()
                due_periods = [period for period in MONITOR_PERIODS if now >= next_runs[period]]
                for period in due_periods:
                    if self._stop_event.is_set():
                        break
                    self._set_status(current_period=period)
                    self._scan_period(service, period, now)
                    next_runs[period] = now + timedelta(minutes=SCAN_INTERVAL_MINUTES[period])
                    self._set_status(last_scan_at=self._format_time(self._now()), last_error=None)
                if due_periods:
                    # 各周期完成信号计算后再裁剪分钟K线，确保计算窗口完整且本地缓存不会持续膨胀。
                    self.database.prune_minute_klines(self._retention_days())
                self._set_status(next_runs={period: self._format_time(value) for period, value in next_runs.items()})
                self._stop_event.wait(1)
        except Exception as exc:
            logger.exception("ETF实时监控线程异常退出: %s", exc)
            self._set_status(last_error=str(exc))
        finally:
            if service is not None:
                service.close()
            self._set_status(
                status="stopped",
                stopped_at=self._format_time(self._now()),
                current_period=None,
                next_runs={},
            )

    def _scan_period(self, service, period, now):
        expected_bar_time = latest_expected_bar_time(period, now)
        if expected_bar_time is None or not self._is_trading_day(now):
            return
        start_date = (now - timedelta(days=180)).strftime("%Y%m%d")
        end_date = now.strftime("%Y%m%d")
        for monitor in self.database.list_realtime_monitors("etf"):
            if self._stop_event.is_set():
                return
            try:
                data = service.get_minute_data(
                    monitor["symbol"],
                    period,
                    start_date,
                    end_date,
                    minimum_trade_time=expected_bar_time,
                )
                prepared = calculate_moving_averages(data)
                completed = prepared[pd.to_datetime(prepared["trade_time"]) <= expected_bar_time].copy()
                if completed.empty or pd.Timestamp(completed["trade_time"].max()) < expected_bar_time:
                    raise ValueError(f"最新{period} K线尚未达到 {expected_bar_time:%Y-%m-%d %H:%M}")
                if len(completed) < MINIMUM_SIGNAL_BARS:
                    raise InsufficientSignalDataError(
                        f"样本不足：{period}均线穿越至少需要{MINIMUM_SIGNAL_BARS}根完整K线，当前仅{len(completed)}根"
                    )
                # 图表保留正在形成的最新K线及其动态均线，穿越信号仍只使用已经完成的K线。
                self.database.save_kline_moving_averages(monitor["symbol"], period, prepared)
                snapshot = build_signal_snapshot(completed)
                self.database.save_realtime_signal_state(monitor, period, snapshot, data.attrs.get("source", "unknown"))
            except Exception as exc:
                logger.warning("实时监控 %s %s 失败: %s", monitor["symbol"], period, exc)
                self.database.save_realtime_signal_error(monitor, period, exc)

    def _is_trading_day(self, now):
        cached = self.database.get_trading_day(now.strftime("%Y-%m-%d"))
        return bool(cached) if cached is not None else now.weekday() < 5

    def _set_status(self, **values):
        with self._lock:
            self._status.update(values)

    @staticmethod
    def _format_time(value):
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else None
