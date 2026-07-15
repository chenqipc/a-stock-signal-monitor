"""新浪分钟行情免费兜底。"""

import json

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from market_data.config import HTTP_TIMEOUT_SECONDS, PROVIDER_MAX_RETRIES
from market_data.exceptions import ProviderUnavailableError


class SinaProvider:
    """提供最近1023根分钟K线，作为盘中行情的第二个免费实时渠道。"""

    name = "sina"
    supported_periods = {"5min", "15min", "30min", "60min", "120min"}
    _scale_map = {"5min": "5", "15min": "15", "30min": "30", "60min": "60"}

    def __init__(self, timeout=HTTP_TIMEOUT_SECONDS):
        self.timeout = timeout
        self.session = requests.Session()
        retry_count = max(0, PROVIDER_MAX_RETRIES - 1)
        retry = Retry(
            total=retry_count,
            connect=retry_count,
            read=retry_count,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def close(self):
        self.session.close()

    def fetch_bars(self, symbol, period, start_date, end_date):
        if period not in self.supported_periods:
            raise ProviderUnavailableError(f"新浪不支持周期: {period}")
        if period == "120min":
            return self._merge_60min_to_120min(self.fetch_bars(symbol, "60min", start_date, end_date))
        url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_market_data=/CN_MarketDataService.getKLineData"
        params = {"symbol": self._normalize_symbol(symbol), "scale": self._scale_map[period], "ma": "no", "datalen": "1023"}
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            rows = self._parse_jsonp(response.text)
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            raise ProviderUnavailableError(f"新浪分钟行情请求失败: {exc}") from exc
        data = pd.DataFrame(rows)
        if data.empty:
            raise ProviderUnavailableError(f"新浪未返回 {symbol} {period} 数据")
        data = data.rename(columns={"day": "trade_time", "volume": "vol"})
        data["trade_time"] = pd.to_datetime(data["trade_time"], errors="coerce")
        for column in ["open", "close", "high", "low", "vol", "amount"]:
            data[column] = pd.to_numeric(data.get(column), errors="coerce")
        data = data.dropna(subset=["trade_time", "close"]).sort_values("trade_time").reset_index(drop=True)
        data["pre_close"] = data["close"].shift(1)
        data["pct_chg"] = data["close"].pct_change() * 100
        data["turnover_rate"] = None
        data["is_st"] = 0
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date) + pd.Timedelta(days=1)
        return data[(data["trade_time"] >= start) & (data["trade_time"] < end)].reset_index(drop=True)

    @staticmethod
    def _normalize_symbol(symbol):
        symbol = str(symbol).strip().lower()
        if symbol.startswith(("sh.", "sz.", "bj.")):
            prefix, code = symbol.split(".", 1)
            return f"{prefix}{code}"
        parts = symbol.split(".")
        code = parts[0]
        if len(parts) > 1:
            return f"{parts[1]}{code}"
        prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
        return f"{prefix}{code}"

    @staticmethod
    def _parse_jsonp(text):
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end < start:
            raise ValueError("新浪响应中没有K线数组")
        return json.loads(text[start:end + 1])

    @staticmethod
    def _merge_60min_to_120min(data):
        if data is None or data.empty:
            return data
        result = data.copy().sort_values("trade_time")
        result["trade_date"] = result["trade_time"].dt.date
        result["group"] = result.groupby("trade_date").cumcount() // 2
        return result.groupby(["trade_date", "group"], as_index=False).agg(
            trade_time=("trade_time", "max"),
            open=("open", "first"),
            close=("close", "last"),
            high=("high", "max"),
            low=("low", "min"),
            vol=("vol", "sum"),
            amount=("amount", "sum"),
            pre_close=("pre_close", "first"),
            pct_chg=("pct_chg", "sum"),
            is_st=("is_st", "max"),
        ).drop(columns=["trade_date", "group"])
