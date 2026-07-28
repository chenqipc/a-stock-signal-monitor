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
MIN_REQUEST_GAP_SECONDS = 2
MINUTE_PRUNE_INTERVAL_MINUTES = 15


class RealtimeMonitorManager:
    """串行拉取分钟行情并持久化信号，避免免费数据源被并发请求压垮。"""

    def __init__(
        self,
        database,
        service_factory=MarketDataService,
        now_provider=datetime.now,
        retention_days_provider=None,
        request_gap_seconds=MIN_REQUEST_GAP_SECONDS,
    ):
        self.database = database
        self.service_factory = service_factory
        self._now = now_provider
        self._retention_days = retention_days_provider or (lambda: DEFAULT_MINUTE_KLINE_RETENTION_DAYS)
        self._request_gap_seconds = max(0, float(request_gap_seconds))
        self._lock = threading.RLock()
        self._network_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._pending_symbols = set()
        self._thread = None
        self._initialization_stop_event = threading.Event()
        self._initialization_symbols = set()
        self._initialization_current = None
        self._initialization_thread = None
        self._status = {
            "status": "stopped",
            "started_at": None,
            "stopped_at": None,
            "last_scan_at": None,
            "current_period": None,
            "last_error": None,
            "next_runs": {},
            "initializing_symbols": [],
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
            self._wake_event.clear()
            self._pending_symbols.clear()
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
            self._wake_event.set()
            return self.get_status()

    def request_scan(self, symbol):
        """将新加入标的交给现有后台线程立即初始化，行情请求仍保持串行。"""
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol:
            return False
        with self._lock:
            if self._status["status"] != "running" or not self._thread or not self._thread.is_alive():
                return False
            self._pending_symbols.add(normalized_symbol)
            self._wake_event.set()
            return True

    def request_initialization(self, symbol):
        """缓存不足时补取四周期数据；实时任务未启动也使用独立串行线程完成一次性初始化。"""
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol or not self._periods_requiring_initialization(normalized_symbol):
            return False
        if self.request_scan(normalized_symbol):
            return True
        with self._lock:
            if normalized_symbol == self._initialization_current or normalized_symbol in self._initialization_symbols:
                return True
            self._initialization_symbols.add(normalized_symbol)
            self._refresh_initialization_status()
            if not self._initialization_thread or not self._initialization_thread.is_alive():
                self._initialization_stop_event.clear()
                self._initialization_thread = threading.Thread(
                    target=self._run_initializations,
                    name="realtime-etf-initializer",
                    daemon=True,
                )
                self._initialization_thread.start()
            return True

    def close(self):
        self.stop()
        self._initialization_stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        initialization_thread = self._initialization_thread
        if initialization_thread and initialization_thread.is_alive():
            initialization_thread.join(timeout=5)

    def get_status(self):
        with self._lock:
            payload = dict(self._status)
            payload["next_runs"] = dict(self._status.get("next_runs") or {})
            payload["initializing_symbols"] = list(self._status.get("initializing_symbols") or [])
            payload["intervals"] = dict(SCAN_INTERVAL_MINUTES)
            return payload

    def _run_initializations(self):
        """消费首次加入ETF的补数队列，不改变实时监控的启停状态。"""
        service = None
        try:
            service = self.service_factory(database=self.database)
            while not self._initialization_stop_event.is_set():
                with self._lock:
                    if not self._initialization_symbols:
                        # 先清空线程引用再退出，避免临界点新增标的后无人消费。
                        self._initialization_thread = None
                        self._initialization_current = None
                        self._refresh_initialization_status()
                        return
                    symbol = min(self._initialization_symbols)
                    self._initialization_symbols.remove(symbol)
                    self._initialization_current = symbol
                    self._refresh_initialization_status()
                self._initialize_symbol(service, symbol)
        except Exception as exc:
            logger.exception("ETF分钟K线初始化线程异常退出: %s", exc)
            self._set_status(last_error=str(exc))
        finally:
            if service is not None:
                service.close()
            with self._lock:
                if self._initialization_thread is threading.current_thread():
                    self._initialization_thread = None
                self._initialization_current = None
                self._refresh_initialization_status()

    def _initialize_symbol(self, service, symbol):
        """仅补取样本不足的周期，并在周期之间留出请求间隔。"""
        monitors = {monitor["symbol"]: monitor for monitor in self.database.list_realtime_monitors("etf")}
        monitor = monitors.get(symbol)
        if monitor is None:
            return
        periods = self._periods_requiring_initialization(symbol)
        for index, period in enumerate(periods):
            if self._initialization_stop_event.is_set():
                return
            if index and self._initialization_stop_event.wait(self._request_gap_seconds):
                return
            self._scan_monitor_period(service, monitor, period, self._now(), allow_historical=True, respect_stop=False)
        self.database.prune_minute_klines(self._retention_days())

    def _run(self):
        service = None
        next_runs = {}
        priority_jobs = set()
        last_scan_started_at = None
        last_pruned_at = None
        try:
            service = self.service_factory(database=self.database)
            while not self._stop_event.is_set():
                now = self._now()
                monitors = self.database.list_realtime_monitors("etf")
                self._sync_instrument_schedules(next_runs, monitors, now)
                pending_symbols = self._take_pending_symbols()
                priority_jobs.update(self._schedule_pending_symbols(next_runs, pending_symbols, monitors, now))
                priority_jobs.intersection_update(next_runs)
                job = self._next_due_job(next_runs, priority_jobs, now, last_scan_started_at)
                if job:
                    symbol, period = job
                    is_priority_initialization = job in priority_jobs
                    self._set_status(current_period=period)
                    last_scan_started_at = now
                    self._scan_period(
                        service,
                        period,
                        now,
                        symbols=(symbol,),
                        allow_historical=is_priority_initialization,
                    )
                    finished_at = self._now()
                    next_runs[job] = finished_at + timedelta(minutes=SCAN_INTERVAL_MINUTES[period])
                    priority_jobs.discard(job)
                    self._set_status(last_scan_at=self._format_time(finished_at), last_error=None)
                    should_prune = last_pruned_at is None or finished_at - last_pruned_at >= timedelta(minutes=MINUTE_PRUNE_INTERVAL_MINUTES)
                    if should_prune:
                        # 分散调度后扫描会持续发生，限制清理频率以避免每只ETF完成后重复裁剪数据库。
                        self.database.prune_minute_klines(self._retention_days())
                        last_pruned_at = finished_at
                self._set_status(next_runs=self._period_next_runs(next_runs))
                self._wake_event.wait(1)
                self._wake_event.clear()
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

    def _scan_period(self, service, period, now, symbols=None, allow_historical=False):
        expected_bar_time = latest_expected_bar_time(period, now) if self._is_trading_day(now) else None
        if expected_bar_time is None and not allow_historical:
            return
        monitors = self.database.list_realtime_monitors("etf")
        if symbols is not None:
            requested_symbols = set(symbols)
            monitors = [monitor for monitor in monitors if monitor["symbol"] in requested_symbols]
        for monitor in monitors:
            if self._stop_event.is_set():
                return
            self._scan_monitor_period(
                service,
                monitor,
                period,
                now,
                expected_bar_time=expected_bar_time,
                allow_historical=allow_historical,
            )

    def _scan_monitor_period(
        self,
        service,
        monitor,
        period,
        now,
        expected_bar_time=None,
        allow_historical=False,
        respect_stop=True,
    ):
        """拉取并计算单个标的周期；首次初始化可在非交易时段补到最近可用交易日。"""
        is_trading_day = self._is_trading_day(now)
        expected_bar_time = expected_bar_time or (latest_expected_bar_time(period, now) if is_trading_day else None)
        if expected_bar_time is None and not allow_historical:
            return False
        fetch_end = now if expected_bar_time is not None else now - timedelta(days=1)
        start_date = (fetch_end - timedelta(days=180)).strftime("%Y%m%d")
        end_date = fetch_end.strftime("%Y%m%d")
        try:
            if respect_stop and self._stop_event.is_set():
                return False
            # 实时调度与首次补数共用网络锁，保证所有免费分钟行情请求严格串行。
            with self._network_lock:
                data = service.get_minute_data(
                    monitor["symbol"],
                    period,
                    start_date,
                    end_date,
                    minimum_trade_time=expected_bar_time,
                )
            prepared = calculate_moving_averages(data)
            completed = prepared.copy()
            if expected_bar_time is not None:
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
            return True
        except Exception as exc:
            logger.warning("实时监控 %s %s 失败: %s", monitor["symbol"], period, exc)
            self.database.save_realtime_signal_error(monitor, period, exc)
            return False

    def _periods_requiring_initialization(self, symbol):
        """61根完整K线才能判断MA60穿越，因此空缓存和短缓存都需要初始化。"""
        return tuple(period for period in MONITOR_PERIODS if len(self.database.load_klines(symbol, period)) < MINIMUM_SIGNAL_BARS)

    def _refresh_initialization_status(self):
        symbols = set(self._initialization_symbols)
        if self._initialization_current:
            symbols.add(self._initialization_current)
        self._status["initializing_symbols"] = sorted(symbols)

    def _take_pending_symbols(self):
        """原子取走本轮待初始化标的；扫描期间新增的标的留给下一轮。"""
        with self._lock:
            symbols = tuple(sorted(self._pending_symbols))
            self._pending_symbols.clear()
            return symbols

    @staticmethod
    def _sync_instrument_schedules(next_runs, monitors, now):
        """为每个ETF建立独立周期，并把同周期首次执行均匀分布在扫描窗口内。"""
        symbols = [monitor["symbol"] for monitor in monitors]
        valid_jobs = {(symbol, period) for symbol in symbols for period in MONITOR_PERIODS}
        for job in tuple(next_runs):
            if job not in valid_jobs:
                next_runs.pop(job, None)
        instrument_count = len(symbols)
        if not instrument_count:
            return
        for instrument_index, symbol in enumerate(symbols):
            for period_index, period in enumerate(MONITOR_PERIODS):
                job = (symbol, period)
                if job in next_runs:
                    continue
                interval_seconds = SCAN_INTERVAL_MINUTES[period] * 60
                instrument_offset = interval_seconds * instrument_index / instrument_count
                period_offset = period_index * MIN_REQUEST_GAP_SECONDS
                next_runs[job] = now + timedelta(seconds=instrument_offset + period_offset)

    @staticmethod
    def _schedule_pending_symbols(next_runs, pending_symbols, monitors, now):
        """新加入ETF优先初始化四周期，各请求仍至少错开最小间隔。"""
        known_symbols = {monitor["symbol"] for monitor in monitors}
        jobs = set()
        for symbol in pending_symbols:
            if symbol not in known_symbols:
                continue
            for period_index, period in enumerate(MONITOR_PERIODS):
                job = (symbol, period)
                next_runs[job] = now + timedelta(seconds=period_index * MIN_REQUEST_GAP_SECONDS)
                jobs.add(job)
        return jobs

    @staticmethod
    def _next_due_job(next_runs, priority_jobs, now, last_scan_started_at):
        """每轮只取一个到期任务，避免多个周期或ETF在同一时刻连续请求。"""
        if last_scan_started_at is not None and now < last_scan_started_at + timedelta(seconds=MIN_REQUEST_GAP_SECONDS):
            return None
        due_jobs = [job for job, scheduled_at in next_runs.items() if scheduled_at <= now]
        if not due_jobs:
            return None
        prioritized = [job for job in due_jobs if job in priority_jobs]
        candidates = prioritized or due_jobs
        return min(candidates, key=lambda job: (next_runs[job], MONITOR_PERIODS.index(job[1]), job[0]))

    def _period_next_runs(self, next_runs):
        """页面仍按周期展示下一次时间，取该周期所有ETF中最早的一项。"""
        payload = {}
        for period in MONITOR_PERIODS:
            scheduled_times = [scheduled_at for (_, job_period), scheduled_at in next_runs.items() if job_period == period]
            if scheduled_times:
                payload[period] = self._format_time(min(scheduled_times))
        return payload

    def _is_trading_day(self, now):
        cached = self.database.get_trading_day(now.strftime("%Y-%m-%d"))
        return bool(cached) if cached is not None else now.weekday() < 5

    def _set_status(self, **values):
        with self._lock:
            self._status.update(values)

    @staticmethod
    def _format_time(value):
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else None
