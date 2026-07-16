"""Yahoo Finance 全球指数免费日线兜底。"""

from datetime import timezone

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from market_data.config import HTTP_TIMEOUT_SECONDS, PROVIDER_MAX_RETRIES
from market_data.exceptions import ProviderUnavailableError


class YahooFinanceProvider:
    """为本项目明确登记的全球指数提供日线，当前用于恒生指数。"""

    name = "yahoo"
    supported_periods = {"D"}
    # 使用项目内部稳定代码映射Yahoo特殊代码，避免上层接口依赖^、=等URL敏感字符。
    _symbol_map = {"HSI.HK": "^HSI", "IXIC.US": "^IXIC"}
    _chart_url = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    def __init__(self, timeout=HTTP_TIMEOUT_SECONDS):
        self.timeout = timeout
        self.session = self._build_session(trust_env=True)
        self.direct_session = self._build_session(trust_env=False)

    @staticmethod
    def _build_session(trust_env):
        session = requests.Session()
        session.trust_env = trust_env
        retry_count = max(0, PROVIDER_MAX_RETRIES - 1)
        retry = Retry(
            total=retry_count,
            connect=retry_count,
            read=retry_count,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        return session

    def close(self):
        self.session.close()
        self.direct_session.close()

    @classmethod
    def supports_symbol(cls, symbol):
        return str(symbol).strip().upper() in cls._symbol_map

    def fetch_bars(self, symbol, period, start_date, end_date):
        normalized = str(symbol).strip().upper()
        if period != "D":
            raise ProviderUnavailableError(f"Yahoo Finance 不支持周期: {period}")
        if not self.supports_symbol(normalized):
            raise ProviderUnavailableError(f"Yahoo Finance 未配置证券代码: {symbol}")
        payload = self._get_json(self._symbol_map[normalized], start_date, end_date)
        return self._parse_chart(payload, normalized, start_date, end_date)

    def _get_json(self, yahoo_symbol, start_date, end_date):
        period_start = int(pd.Timestamp(start_date, tz=timezone.utc).timestamp())
        period_end = int((pd.Timestamp(end_date, tz=timezone.utc) + pd.Timedelta(days=1)).timestamp())
        url = self._chart_url.format(symbol=requests.utils.quote(yahoo_symbol, safe=""))
        params = {"period1": period_start, "period2": period_end, "interval": "1d", "events": "history"}
        errors = []
        for label, session in (("代理", self.session), ("直连", self.direct_session)):
            try:
                response = session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                errors.append(f"{label}: {exc}")
        raise ProviderUnavailableError(f"Yahoo Finance 日线请求失败: {'; '.join(errors)}")

    @staticmethod
    def _parse_chart(payload, symbol, start_date, end_date):
        chart = payload.get("chart") or {}
        if chart.get("error"):
            raise ProviderUnavailableError(f"Yahoo Finance 返回错误: {chart['error']}")
        results = chart.get("result") or []
        if not results:
            raise ProviderUnavailableError(f"Yahoo Finance 未返回 {symbol} 日线数据")
        result = results[0]
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        if not timestamps:
            raise ProviderUnavailableError(f"Yahoo Finance 未返回 {symbol} 日线数据")
        data = pd.DataFrame({"trade_time": pd.to_datetime(timestamps, unit="s", utc=True)})
        for source, target in (("open", "open"), ("close", "close"), ("high", "high"), ("low", "low"), ("volume", "vol")):
            values = quote.get(source) or []
            data[target] = pd.Series(values, dtype="float64").reindex(range(len(data)))
        data["trade_time"] = data["trade_time"].dt.tz_convert("Asia/Hong_Kong").dt.tz_localize(None).dt.normalize()
        data["amount"] = None
        # Yahoo 偶尔会返回尚未形成K线的空交易日，先清理再计算昨收和涨跌幅，避免最新一日出现 NaN。
        data = data.dropna(subset=["trade_time", "close"]).sort_values("trade_time").reset_index(drop=True)
        data["pre_close"] = data["close"].shift(1)
        data["pct_chg"] = data["close"].pct_change(fill_method=None) * 100
        data["turnover_rate"] = None
        data["is_st"] = 0
        start = pd.Timestamp(start_date).normalize()
        end = pd.Timestamp(end_date).normalize()
        return data[data["trade_time"].between(start, end)].reset_index(drop=True)
