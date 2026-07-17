"""首页主要指数缓存、刷新与历史分页服务。"""

import threading
from datetime import datetime, time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd

from market_data.database import MarketDataDatabase
from market_data.service import MarketDataService
from market_data.trading_calendar import TRADING_SESSIONS


INDEX_REFRESH_COOLDOWN_SECONDS = 300
INDEX_INTRADAY_CACHE_SECONDS = 120
INDEX_MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
INDEX_DEFAULT_DAILY_READY_TIME = time(15, 30)
INDEX_DAILY_READY_TIMES = {"HSI.HK": time(16, 30)}
INDEX_WATCHLIST = (
    {"symbol": "000001.SH", "name": "上证指数", "short_name": "上证"},
    {"symbol": "399001.SZ", "name": "深证成指", "short_name": "深成指"},
    {"symbol": "399006.SZ", "name": "创业板指", "short_name": "创业板"},
    {"symbol": "000300.SH", "name": "沪深300指数", "short_name": "沪深300"},
    {"symbol": "000688.SH", "name": "科创50指数", "short_name": "科创50"},
    {"symbol": "HSI.HK", "name": "恒生指数", "short_name": "恒生"},
    {"symbol": "IXIC.US", "name": "纳斯达克综合指数", "short_name": "纳斯达克", "allow_current": False},
    {"symbol": "GOLD.SGE", "name": "Au99.99现货黄金（延时）", "short_name": "黄金现货"},
)


class IndexMarketService:
    """封装指数专用缓存策略，使Flask路由只负责HTTP参数与响应。"""

    def __init__(
        self,
        database: MarketDataDatabase,
        market_service_factory: Callable[..., MarketDataService] = MarketDataService,
        now_provider: Callable[[], datetime] = datetime.now,
    ):
        self.database = database
        self.market_service_factory = market_service_factory
        self.now = now_provider
        self.refresh_failures = {}
        self.refresh_lock = threading.Lock()

    @property
    def symbols(self) -> set[str]:
        return {item["symbol"] for item in INDEX_WATCHLIST}

    def load_watchlist(self, limit: int, refresh_missing: bool = False) -> list[dict]:
        if refresh_missing:
            with self.refresh_lock:
                return self._load_watchlist(limit, refresh_missing=True)
        return self._load_watchlist(limit, refresh_missing=False)

    def load_history(self, symbol: str, before: pd.Timestamp, limit: int, ensure_data: bool = True) -> dict:
        if symbol not in self.symbols:
            raise ValueError("该标的不属于首页主要指数")
        with self.refresh_lock:
            return self._load_history(symbol, before, limit, ensure_data)

    def _load_watchlist(self, limit: int, refresh_missing: bool) -> list[dict]:
        end_date = self.now().date()
        start_date = end_date - timedelta(days=max(180, limit * 2))
        service = self.market_service_factory(database=self.database)
        try:
            return [
                self._build_item(service, item, start_date, end_date, limit, refresh_missing)
                for item in INDEX_WATCHLIST
            ]
        finally:
            service.close()

    def _build_item(self, service, item, start_date, end_date, limit, refresh_missing):
        source = "sqlite_cache"
        error = None
        data = self.database.load_klines(item["symbol"], "D", start_date, end_date)
        allow_current = item.get("allow_current", True)
        needs_refresh = self._data_needs_refresh(item["symbol"], data, end_date, allow_current)
        if needs_refresh and self._refresh_is_cooling(item["symbol"]):
            needs_refresh = False
        try:
            if refresh_missing and needs_refresh and not self._refresh_is_cooling(item["symbol"]):
                target_date = self._latest_target_date(end_date, allow_current)
                data = service.get_daily_data(
                    item["symbol"], start_date, target_date.date(), minimum_trade_time=target_date, refresh_latest=True
                )
                source = data.attrs.get("source")
                still_stale = self._data_needs_refresh(item["symbol"], data, end_date, allow_current)
                if still_stale:
                    self._remember_refresh_failure(item["symbol"])
                    provider_errors = data.attrs.get("provider_errors") or []
                    error = "; ".join(provider_errors) or "行情源尚未返回当日指数数据"
                else:
                    self._clear_refresh_failure(item["symbol"])
                needs_refresh = still_stale and not self._refresh_is_cooling(item["symbol"])
        except Exception as exc:
            self._remember_refresh_failure(item["symbol"])
            error = str(exc)
            data = self.database.load_klines(item["symbol"], "D", start_date, end_date)
            source = "sqlite_stale_cache" if not data.empty else "unavailable"
            needs_refresh = data.empty and not self._refresh_is_cooling(item["symbol"])
        if data.empty:
            source = "unavailable"
        data = data.tail(limit)
        points = frame_records(data)
        latest = points[-1] if points else {}
        return {
            **item,
            "close": latest.get("close"),
            "pct_chg": latest.get("pct_chg"),
            "range_pct": range_percent(data),
            "trade_time": latest.get("trade_time"),
            "source": source,
            "error": error,
            "needs_refresh": needs_refresh,
            "points": points,
        }

    def _load_history(self, symbol, before, limit, ensure_data):
        end_date = before.normalize() - pd.Timedelta(1, unit="D")
        start_date = end_date - pd.Timedelta(max(365, limit * 3), unit="D")
        data = self.database.load_klines(symbol, "D", start_date, end_date)
        source = "sqlite_cache" if not data.empty else "unavailable"
        warning = None
        if ensure_data and len(data) < limit:
            service = self.market_service_factory(database=self.database)
            try:
                data = service.get_daily_data(symbol, start_date, end_date)
                source = data.attrs.get("source", source)
                provider_errors = data.attrs.get("provider_errors") or []
                warning = "; ".join(provider_errors) if provider_errors else None
            except Exception as exc:
                warning = str(exc)
                data = self.database.load_klines(symbol, "D", start_date, end_date)
                source = "sqlite_stale_cache" if not data.empty else "unavailable"
            finally:
                service.close()
        data = data[data["trade_time"] < before].tail(limit) if not data.empty else data
        points = frame_records(data)
        return {
            "symbol": symbol,
            "period": "D",
            "source": source,
            "warning": warning,
            "has_more": len(points) >= limit,
            "items": points,
        }

    def _refresh_is_cooling(self, symbol):
        failure_time = self.refresh_failures.get(symbol)
        return bool(failure_time and (self.now() - failure_time).total_seconds() < INDEX_REFRESH_COOLDOWN_SECONDS)

    def _remember_refresh_failure(self, symbol):
        self.refresh_failures[symbol] = self.now()

    def _clear_refresh_failure(self, symbol):
        self.refresh_failures.pop(symbol, None)

    def _data_needs_refresh(self, symbol, data, end_date, allow_current=True):
        if data is None or data.empty:
            return True
        expected_date = self._latest_target_date(end_date, allow_current)
        latest = pd.to_datetime(data["trade_time"]).max().normalize()
        state = self.database.get_fetch_state(symbol, "D") or {}
        ready_time = INDEX_DAILY_READY_TIMES.get(symbol, INDEX_DEFAULT_DAILY_READY_TIME)
        if allow_current and self._target_is_intraday(expected_date, ready_time):
            return latest < expected_date or not index_cache_is_recent(state, self.now())
        checked_end = pd.Timestamp(state["coverage_end"]).normalize() if state.get("coverage_end") else None
        if latest < expected_date:
            # 已知交易日缺少目标K线时不能仅凭coverage_end判定完成，旧版本可能写入过超前覆盖日期。
            if self.database.get_trading_day(expected_date.date()) is True:
                return True
            return checked_end is None or checked_end < expected_date
        # 当天日线可能只是盘中快照；收盘后必须确认它是在收盘数据就绪时间之后重新获取的。
        return allow_current and latest == expected_date and not index_daily_cache_is_final(state, expected_date, ready_time)

    def _latest_target_date(self, end_date, allow_current=True):
        now = self.now()
        candidate = min(pd.Timestamp(end_date).date(), now.date())
        if candidate == now.date() and (not allow_current or now.time() < TRADING_SESSIONS[0][0]):
            candidate -= timedelta(days=1)
        for _ in range(15):
            trading_day = self.database.get_trading_day(candidate)
            if trading_day is None:
                trading_day = candidate.weekday() < 5
            if trading_day:
                return pd.Timestamp(candidate)
            candidate -= timedelta(days=1)
        return pd.Timestamp(candidate)

    def _target_is_intraday(self, expected_date, ready_time):
        now = self.now()
        return pd.Timestamp(expected_date).date() == now.date() and TRADING_SESSIONS[0][0] <= now.time() < ready_time


def index_cache_is_recent(state, now=None):
    last_success_at = state.get("last_success_at")
    if not last_success_at:
        return False
    updated_at = pd.Timestamp(last_success_at)
    if updated_at.tzinfo is None:
        updated_at = updated_at.tz_localize("UTC")
    current = pd.Timestamp(now or datetime.now())
    if current.tzinfo is None:
        current = current.tz_localize(INDEX_MARKET_TIMEZONE)
    return current.tz_convert("UTC") - updated_at.tz_convert("UTC") <= pd.Timedelta(seconds=INDEX_INTRADAY_CACHE_SECONDS)


def index_daily_cache_is_final(state, target_date, ready_time):
    """只有在目标交易日收盘数据就绪后成功写入，才把当日日线视为最终值。"""
    last_success_at = state.get("last_success_at")
    if not last_success_at:
        return False
    updated_at = pd.Timestamp(last_success_at)
    if updated_at.tzinfo is None:
        updated_at = updated_at.tz_localize("UTC")
    ready_at = pd.Timestamp(datetime.combine(pd.Timestamp(target_date).date(), ready_time)).tz_localize(INDEX_MARKET_TIMEZONE)
    return updated_at.tz_convert(INDEX_MARKET_TIMEZONE) >= ready_at


def range_percent(data):
    if data is None or data.empty or len(data) < 2:
        return None
    closes = pd.to_numeric(data["close"], errors="coerce").dropna()
    if len(closes) < 2 or closes.iloc[0] == 0:
        return None
    return (closes.iloc[-1] / closes.iloc[0] - 1) * 100


def frame_records(data):
    if data is None or data.empty:
        return []
    normalized = data.copy()
    for column in normalized.columns:
        if pd.api.types.is_datetime64_any_dtype(normalized[column]):
            normalized[column] = normalized[column].dt.strftime("%Y-%m-%d %H:%M:%S")
    normalized = normalized.astype(object).where(pd.notna(normalized), None)
    return normalized.to_dict(orient="records")
