"""多数据源降级、增量缓存和证券列表服务。"""

import logging
from datetime import datetime, time, timedelta, timezone

import pandas as pd

from market_data.config import (
    DAILY_CACHE_OVERLAP_BARS,
    ENABLE_TUSHARE_FALLBACK,
    MIN_ETF_LIST_SIZE,
    MIN_STOCK_LIST_SIZE,
    MINUTE_CACHE_TTL_SECONDS,
    PROVIDER_MAX_RETRIES,
    PROVIDER_RETRY_MAX_DELAY_SECONDS,
    TUSHARE_DAILY_REQUEST_LIMIT,
    TUSHARE_REQUESTS_PER_MINUTE,
    get_database_journal_mode,
    get_database_path,
    get_tushare_tokens,
)
from market_data.database import MarketDataDatabase
from market_data.exceptions import MarketDataError, ProviderUnavailableError
from market_data.providers import (
    BaoStockProvider,
    EastMoneyProvider,
    ShanghaiGoldExchangeProvider,
    SinaProvider,
    TencentProvider,
    YahooFinanceProvider,
)


logger = logging.getLogger(__name__)
DAILY_BAR_READY_TIME = time(15, 30)


class MarketDataService:
    """对调用方屏蔽行情来源，并在网络失败时回退到本地历史数据。"""

    def __init__(
        self,
        database=None,
        daily_providers=None,
        minute_providers=None,
        max_retries=PROVIDER_MAX_RETRIES,
        minimum_stock_count=MIN_STOCK_LIST_SIZE,
        minimum_etf_count=MIN_ETF_LIST_SIZE,
        now_provider=None,
    ):
        self.database = database or MarketDataDatabase(get_database_path(), get_database_journal_mode())
        self.max_retries = max(1, max_retries)
        self.minimum_stock_count = max(1, minimum_stock_count)
        self.minimum_etf_count = max(1, minimum_etf_count)
        self._now = now_provider or datetime.now
        baostock_provider = BaoStockProvider() if daily_providers is None or minute_providers is None else None
        eastmoney_provider = EastMoneyProvider() if daily_providers is None or minute_providers is None else None
        yahoo_provider = YahooFinanceProvider() if daily_providers is None else None
        sge_provider = ShanghaiGoldExchangeProvider() if daily_providers is None else None
        sina_provider = SinaProvider() if daily_providers is None or minute_providers is None else None
        tencent_provider = TencentProvider() if daily_providers is None else None
        # 各专用源通过supports_symbol跳过无关证券，A股保持BaoStock -> 新浪 -> 腾讯 -> 东方财富的降级顺序。
        self.daily_providers = daily_providers if daily_providers is not None else [
            baostock_provider, yahoo_provider, sge_provider, sina_provider, tencent_provider, eastmoney_provider
        ]
        self.minute_providers = minute_providers if minute_providers is not None else [
            baostock_provider,
            eastmoney_provider,
            sina_provider,
        ]
        if daily_providers is None:
            from market_data.providers.tushare_provider import TushareProvider

            tushare_tokens = get_tushare_tokens()
            if tushare_tokens:
                self.daily_providers.append(TushareProvider(tushare_tokens, TUSHARE_REQUESTS_PER_MINUTE, TUSHARE_DAILY_REQUEST_LIMIT))
            elif ENABLE_TUSHARE_FALLBACK:
                logger.warning("已启用Tushare兜底，但未在环境变量或本机私密配置中找到Token")

    def get_bars(
        self, symbol, period, start_date, end_date, force_refresh=False, minimum_trade_time=None, refresh_latest=False
    ):
        normalized_symbol = self.normalize_symbol(symbol)
        cached = self.database.load_klines(normalized_symbol, period, start_date, end_date)
        daily_target = self._latest_completed_daily_date(end_date) if period == "D" else None
        if period == "D" and refresh_latest:
            daily_target = pd.Timestamp(min(pd.Timestamp(end_date).date(), self._now().date()))
        daily_start_covered = False
        if period == "D":
            daily_start_covered = self._daily_start_is_covered(normalized_symbol, cached, start_date)
            cache_ready = self._daily_cache_covers(normalized_symbol, cached, start_date, daily_target)
            cache_ready = cache_ready and self._daily_data_has_chart_fields(cached)
            cache_ready = cache_ready and self._meets_minimum_trade_time(cached, minimum_trade_time)
        else:
            cache_ready = self._cache_is_fresh(normalized_symbol, period, MINUTE_CACHE_TTL_SECONDS)
            cache_ready = cache_ready and self._cache_covers_range(cached, start_date, end_date, period)
            cache_ready = cache_ready and self._meets_minimum_trade_time(cached, minimum_trade_time)
        if not force_refresh and not refresh_latest and not cached.empty and cache_ready:
            result = self._prepare_result(cached)
            result.attrs["source"] = "sqlite_cache"
            result.attrs["cached_sources"] = sorted(cached["source"].dropna().unique().tolist())
            return result

        if period == "D":
            fetch_start = start_date if force_refresh or not daily_start_covered else self._daily_incremental_start(cached, start_date)
            fetch_end = daily_target
        else:
            fetch_start = self._incremental_start(cached, start_date, period)
            fetch_end = end_date
        providers = self.daily_providers if period == "D" else self.minute_providers
        errors = []
        for provider in providers:
            if period not in provider.supported_periods:
                continue
            if hasattr(provider, "supports_symbol") and not provider.supports_symbol(normalized_symbol):
                continue
            try:
                fresh = self._fetch_with_retry(provider, normalized_symbol, period, fetch_start, fetch_end)
                if fresh is None or fresh.empty:
                    raise ProviderUnavailableError("返回空数据")
                if not self._provider_data_is_fresh(fresh, period, fetch_end, minimum_trade_time):
                    freshness_error = "日线行情未包含目标交易日数据" if period == "D" else "分钟行情未包含当前交易日数据"
                    raise ProviderUnavailableError(freshness_error)
                actual_fetch_start = fetch_start
                if period == "D" and not force_refresh and self._adjusted_history_changed(cached, fresh):
                    # 除权除息会改变前复权历史价格，仅在检测到变化时重取完整策略窗口。
                    logger.info("检测到 %s 前复权历史变化，刷新完整日线窗口", normalized_symbol)
                    actual_fetch_start = start_date
                    fresh = self._fetch_with_retry(provider, normalized_symbol, period, actual_fetch_start, fetch_end)
                    if fresh is None or fresh.empty:
                        raise ProviderUnavailableError("复权历史刷新返回空数据")
                self.database.save_klines(
                    normalized_symbol,
                    period,
                    fresh,
                    provider.name,
                    coverage_start=actual_fetch_start,
                    coverage_end=fetch_end,
                )
                merged = self.database.load_klines(normalized_symbol, period, start_date, end_date)
                result = self._prepare_result(merged)
                result.attrs["source"] = provider.name
                return result
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
                log_method = logger.info if self._is_expected_provider_miss(provider, period, exc) else logger.warning
                log_method("行情源 %s 获取 %s %s 失败: %s", provider.name, normalized_symbol, period, exc)

        if not cached.empty:
            cached = self._prepare_result(cached)
            cached.attrs["source"] = "sqlite_stale_cache"
            cached.attrs["provider_errors"] = errors
            return cached
        raise MarketDataError(f"所有行情源均不可用，{normalized_symbol} {period}: {'; '.join(errors)}")

    @staticmethod
    def _is_expected_provider_miss(provider, period, exc):
        message = str(exc)
        return provider.name == "baostock" and period == "D" and (
            "日线行情未包含目标交易日数据" in message or "返回空数据" in message
        )

    def get_daily_data(self, symbol, start_date, end_date, force_refresh=False, minimum_trade_time=None, refresh_latest=False):
        return self.get_bars(symbol, "D", start_date, end_date, force_refresh, minimum_trade_time, refresh_latest)

    def daily_cache_ready(self, symbol, start_date, end_date):
        normalized_symbol = self.normalize_symbol(symbol)
        cached = self.database.load_klines(normalized_symbol, "D", start_date, end_date)
        target = self._latest_completed_daily_date(end_date)
        return self._daily_data_has_chart_fields(cached) and self._daily_cache_covers(normalized_symbol, cached, start_date, target)

    def get_cached_daily_data(self, symbol, start_date, end_date):
        normalized_symbol = self.normalize_symbol(symbol)
        cached = self.database.load_klines(normalized_symbol, "D", start_date, end_date)
        result = self._prepare_result(cached)
        result.attrs["source"] = "sqlite_cache"
        return result

    def get_minute_data(self, symbol, period, start_date, end_date, force_refresh=False, minimum_trade_time=None):
        return self.get_bars(symbol, period, start_date, end_date, force_refresh, minimum_trade_time)

    def _fetch_with_retry(self, provider, symbol, period, start_date, end_date):
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return provider.fetch_bars(symbol, period, start_date, end_date)
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    delay_seconds = min(2 ** attempt, PROVIDER_RETRY_MAX_DELAY_SECONDS)
                    import time

                    time.sleep(delay_seconds)
        raise ProviderUnavailableError(str(last_error)) from last_error

    def get_trade_calendar(self, start_date, end_date):
        for provider in self.daily_providers:
            if not hasattr(provider, "fetch_trade_calendar"):
                continue
            try:
                data = provider.fetch_trade_calendar(start_date, end_date)
                if data is not None and not data.empty:
                    data.attrs["source"] = provider.name
                    self.database.save_trade_calendar(data, provider.name)
                    return data
            except Exception as exc:
                logger.warning("交易日历源 %s 不可用: %s", provider.name, exc)
        dates = pd.date_range(start_date, end_date, freq="D")
        data = pd.DataFrame(
            {"calendar_date": dates.strftime("%Y-%m-%d"), "is_trading_day": (dates.weekday < 5).astype(int)}
        )
        data.attrs["source"] = "weekday_fallback"
        return data

    def is_trading_day(self, value):
        calendar_date = pd.Timestamp(value).strftime("%Y-%m-%d")
        cached = self.database.get_trading_day(calendar_date)
        if cached is not None:
            return cached
        data = self.get_trade_calendar(calendar_date, calendar_date)
        if data.empty:
            return pd.Timestamp(value).weekday() < 5
        self.database.save_trade_calendar(data, data.attrs.get("source", "weekday_fallback"))
        return bool(int(data.iloc[0]["is_trading_day"]))

    def get_stock_list(self, force_refresh=False):
        cached = self.database.load_stock_list()
        if not force_refresh and not cached.empty:
            cached.attrs["source"] = "sqlite_cache"
            return cached

        errors = []
        for provider in self.daily_providers:
            if not hasattr(provider, "fetch_stock_list"):
                continue
            try:
                data = provider.fetch_stock_list()
                if data is not None and len(data) >= self.minimum_stock_count:
                    self.database.replace_stock_list(data, provider.name)
                    result = self.database.load_stock_list()
                    result.attrs["source"] = provider.name
                    return result
                actual_count = 0 if data is None else len(data)
                raise ProviderUnavailableError(
                    f"证券列表仅返回{actual_count}条，低于完整性阈值{self.minimum_stock_count}条"
                )
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
                logger.warning("证券列表源 %s 不可用: %s", provider.name, exc)
        if not cached.empty:
            cached.attrs["source"] = "sqlite_stale_cache"
            cached.attrs["provider_errors"] = errors
            return cached
        raise MarketDataError(f"证券列表获取失败: {'; '.join(errors)}")

    def get_etf_list(self, force_refresh=False):
        """获取ETF主列表；网络不可用时继续使用SQLite中的最近快照。"""
        cached = self.database.load_etf_list()
        if not force_refresh and not cached.empty:
            cached.attrs["source"] = "sqlite_cache"
            return cached
        errors = []
        for provider in self.daily_providers:
            if not hasattr(provider, "fetch_etf_list"):
                continue
            try:
                data = provider.fetch_etf_list()
                if data is not None and len(data) >= self.minimum_etf_count:
                    self.database.replace_etf_list(data, provider.name)
                    result = self.database.load_etf_list()
                    result.attrs["source"] = provider.name
                    return result
                actual_count = 0 if data is None else len(data)
                raise ProviderUnavailableError(f"ETF列表仅返回{actual_count}条，低于完整性阈值{self.minimum_etf_count}条")
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
                logger.warning("ETF列表源 %s 不可用: %s", provider.name, exc)
        if not cached.empty:
            cached.attrs["source"] = "sqlite_stale_cache"
            cached.attrs["provider_errors"] = errors
            return cached
        raise MarketDataError(f"ETF列表获取失败: {'; '.join(errors)}")

    def close(self):
        closed = set()
        for provider in self.daily_providers + self.minute_providers:
            if id(provider) in closed:
                continue
            closed.add(id(provider))
            provider.close()

    @staticmethod
    def normalize_symbol(symbol):
        symbol = str(symbol).strip().upper()
        if symbol.startswith(("SH.", "SZ.", "BJ.")):
            prefix, code = symbol.split(".", 1)
            return f"{code}.{prefix}"
        if "." in symbol:
            return symbol
        if symbol.startswith("92"):
            return f"{symbol}.BJ"
        if symbol.startswith(("5", "6", "9")):
            return f"{symbol}.SH"
        if symbol.startswith(("4", "8")):
            return f"{symbol}.BJ"
        return f"{symbol}.SZ"

    @staticmethod
    def _incremental_start(cached, requested_start, period):
        if cached.empty:
            return requested_start
        requested_start = pd.Timestamp(requested_start)
        if cached["trade_time"].min() > requested_start + timedelta(days=7):
            return requested_start
        latest = cached["trade_time"].max()
        overlap = timedelta(days=5 if period == "D" else 1)
        return max(requested_start, latest - overlap)

    @staticmethod
    def _daily_incremental_start(cached, requested_start):
        """只重取最近少量已完成日线，用于校验修正和复权变化。"""
        requested_start = pd.Timestamp(requested_start)
        if cached.empty:
            return requested_start
        trade_times = cached["trade_time"].dropna().drop_duplicates().sort_values().reset_index(drop=True)
        overlap_index = max(0, len(trade_times) - DAILY_CACHE_OVERLAP_BARS)
        return max(requested_start, trade_times.iloc[overlap_index])

    def _daily_cache_covers(self, symbol, cached, requested_start, target_date):
        if cached.empty or target_date is None:
            return False
        state = self.database.get_fetch_state(symbol, "D") or {}
        checked_end = pd.Timestamp(state["coverage_end"]) if state.get("coverage_end") else None
        start_covered = self._daily_start_is_covered(symbol, cached, requested_start)
        end_covered = cached["trade_time"].max().normalize() >= target_date.normalize()
        end_covered = end_covered or (checked_end is not None and checked_end.normalize() >= target_date.normalize())
        return start_covered and end_covered

    def _daily_start_is_covered(self, symbol, cached, requested_start):
        if cached.empty:
            return False
        requested_start = pd.Timestamp(requested_start)
        state = self.database.get_fetch_state(symbol, "D") or {}
        checked_start = pd.Timestamp(state["coverage_start"]) if state.get("coverage_start") else None
        return cached["trade_time"].min() <= requested_start + timedelta(days=7) or (
            checked_start is not None and checked_start <= requested_start
        )

    def _latest_completed_daily_date(self, end_date):
        """计算请求范围内最近一个已经完成并可稳定获取的交易日。"""
        requested_end = pd.Timestamp(end_date).date()
        now = self._now()
        candidate = min(requested_end, now.date())
        current_time = now.time().replace(tzinfo=None)
        if candidate == now.date() and current_time < DAILY_BAR_READY_TIME:
            candidate -= timedelta(days=1)
        for _ in range(15):
            if self.is_trading_day(candidate):
                return pd.Timestamp(candidate)
            candidate -= timedelta(days=1)
        return pd.Timestamp(candidate)

    @staticmethod
    def _adjusted_history_changed(cached, fresh):
        """通过重叠K线识别前复权价格是否因除权除息发生变化。"""
        if cached.empty or fresh.empty:
            return False
        price_columns = ["open", "close", "high", "low", "pre_close"]
        available_columns = [column for column in price_columns if column in cached and column in fresh]
        overlap = cached[["trade_time", *available_columns]].merge(
            fresh[["trade_time", *available_columns]], on="trade_time", suffixes=("_cached", "_fresh")
        )
        for column in available_columns:
            cached_values = pd.to_numeric(overlap[f"{column}_cached"], errors="coerce")
            fresh_values = pd.to_numeric(overlap[f"{column}_fresh"], errors="coerce")
            tolerance = (cached_values.abs() * 0.0001).clip(lower=0.0001)
            if ((cached_values - fresh_values).abs() > tolerance).fillna(False).any():
                return True
        return False

    @staticmethod
    def _daily_data_has_volume(data):
        """成交量是策略计算和日线详情的基础字段，旧缓存完全缺失时必须重新补取。"""
        return bool(data is not None and not data.empty and "vol" in data.columns and data["vol"].notna().any())

    @staticmethod
    def _daily_data_has_chart_fields(data):
        """专业日线图和策略计算共同依赖OHLCV，任一核心字段完全缺失都需要补数。"""
        required_columns = ("open", "high", "low", "close", "vol")
        return data is not None and not data.empty and all(column in data and data[column].notna().any() for column in required_columns)

    @staticmethod
    def _cache_covers_range(cached, start_date, end_date, period):
        tolerance = timedelta(days=7 if period == "D" else 3)
        requested_start = pd.Timestamp(start_date)
        requested_end = pd.Timestamp(end_date)
        return cached["trade_time"].min() <= requested_start + tolerance and cached["trade_time"].max() >= requested_end - tolerance

    @staticmethod
    def _provider_data_is_fresh(data, period, end_date, minimum_trade_time=None):
        if not MarketDataService._meets_minimum_trade_time(data, minimum_trade_time):
            return False
        if period == "D" or pd.Timestamp(end_date).date() != datetime.now().date():
            return True
        return data["trade_time"].max().date() == datetime.now().date()

    @staticmethod
    def _meets_minimum_trade_time(data, minimum_trade_time):
        return minimum_trade_time is None or data["trade_time"].max() >= pd.Timestamp(minimum_trade_time)

    def _cache_is_fresh(self, symbol, period, ttl_seconds):
        state = self.database.get_fetch_state(symbol, period)
        if not state:
            return False
        updated_at = datetime.fromisoformat(state["last_success_at"])
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - updated_at <= timedelta(seconds=ttl_seconds)

    @staticmethod
    def _prepare_result(data):
        result = data.copy()
        result = result.sort_values("trade_time").drop_duplicates("trade_time", keep="last").reset_index(drop=True)
        result["trade_date"] = result["trade_time"].dt.strftime("%Y%m%d")
        return result


_default_service = None


def get_default_service():
    global _default_service
    if _default_service is None:
        _default_service = MarketDataService()
    return _default_service
