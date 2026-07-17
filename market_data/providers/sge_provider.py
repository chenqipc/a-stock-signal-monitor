"""上海黄金交易所Au99.99现货黄金日线行情源。"""

import html
import re

import pandas as pd
import requests

from market_data.config import HTTP_TIMEOUT_SECONDS
from market_data.exceptions import ProviderUnavailableError


class ShanghaiGoldExchangeProvider:
    """从上金所官方接口读取Au99.99现货实盘合约的OHLC日线。"""

    name = "sge"
    supported_periods = {"D"}
    _symbol = "GOLD.SGE"
    _instrument = "Au99.99"
    _history_url = "https://www.sge.com.cn/graph/Dailyhq"
    _delayed_quote_url = "https://www.sge.com.cn/h5_sjzx/yshq"

    def __init__(self, timeout=HTTP_TIMEOUT_SECONDS):
        self.timeout = timeout
        self.session = self._build_session(trust_env=True)
        self.direct_session = self._build_session(trust_env=False)

    @staticmethod
    def _build_session(trust_env):
        session = requests.Session()
        session.trust_env = trust_env
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.sge.com.cn/sjzx/mrhq",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        return session

    def close(self):
        self.session.close()
        self.direct_session.close()

    @classmethod
    def supports_symbol(cls, symbol):
        return str(symbol).strip().upper() == cls._symbol

    def fetch_bars(self, symbol, period, start_date, end_date):
        if period != "D" or not self.supports_symbol(symbol):
            raise ProviderUnavailableError(f"上海黄金交易所不支持 {symbol} {period}")
        rows = self._request_history()
        if not rows:
            raise ProviderUnavailableError("上海黄金交易所未返回Au99.99日线数据")
        data = self._parse_history(rows, start_date, end_date)
        try:
            delayed_quote = self._request_delayed_quote()
        except ProviderUnavailableError:
            delayed_quote = None
        if delayed_quote and pd.Timestamp(start_date) <= pd.Timestamp(delayed_quote["trade_time"]) <= pd.Timestamp(end_date):
            # 延时行情只用于更新当日正在形成的K线，完整日线发布后会按日期覆盖这条快照。
            data = pd.concat([data, pd.DataFrame([delayed_quote])], ignore_index=True)
            data = self._finalize_bars(data)
        return data

    def _request_history(self):
        response = self._request("post", self._history_url, "日线", data={"instid": self._instrument})
        try:
            return (response.json() or {}).get("time") or []
        except ValueError as exc:
            raise ProviderUnavailableError(f"上海黄金交易所日线响应解析失败: {exc}") from exc

    def _request_delayed_quote(self):
        response = self._request("get", self._delayed_quote_url, "延时行情")
        return self._parse_delayed_quote(response.text)

    def _request(self, method, url, label, **kwargs):
        errors = []
        for route, session in (("代理", self.session), ("直连", self.direct_session)):
            try:
                response = getattr(session, method)(url, timeout=self.timeout, **kwargs)
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                errors.append(f"{route}: {exc}")
        raise ProviderUnavailableError(f"上海黄金交易所{label}请求失败: {'; '.join(errors)}")

    @staticmethod
    def _parse_history(rows, start_date, end_date):
        data = pd.DataFrame(rows, columns=["trade_time", "open", "close", "low", "high"])
        data = ShanghaiGoldExchangeProvider._finalize_bars(data)
        start = pd.Timestamp(start_date).normalize()
        end = pd.Timestamp(end_date).normalize()
        return data[data["trade_time"].between(start, end)].reset_index(drop=True)

    @staticmethod
    def _parse_delayed_quote(content):
        date_match = re.search(r"(\d{4})年(\d{2})月(\d{2})日延时行情", content)
        row_match = re.search(r"<tr[^>]*>\s*<td[^>]*>Au99\.99</td>(.*?)</tr>", content, flags=re.DOTALL | re.IGNORECASE)
        if not date_match or not row_match:
            raise ProviderUnavailableError("上海黄金交易所延时行情中没有Au99.99数据")
        values = [html.unescape(re.sub(r"<[^>]+>", "", value)).strip() for value in re.findall(r"<td[^>]*>(.*?)</td>", row_match.group(1), re.DOTALL)]
        if len(values) < 4:
            raise ProviderUnavailableError("上海黄金交易所Au99.99延时行情字段不完整")
        year, month, day = date_match.groups()
        return {
            "trade_time": f"{year}-{month}-{day}",
            "close": values[0],
            "high": values[1],
            "low": values[2],
            "open": values[3],
            "vol": 0.0,
            "amount": 0.0,
            "turnover_rate": None,
            "is_st": 0,
        }

    @staticmethod
    def _finalize_bars(data):
        data = data.copy()
        data["trade_time"] = pd.to_datetime(data["trade_time"], errors="coerce")
        for column in ("open", "close", "low", "high"):
            data[column] = pd.to_numeric(data[column], errors="coerce")
        data = data.dropna(subset=["trade_time", "open", "close", "low", "high"])
        data = data.sort_values("trade_time").drop_duplicates("trade_time", keep="last").reset_index(drop=True)
        data["pre_close"] = data["close"].shift(1)
        data["pct_chg"] = data["close"].pct_change(fill_method=None) * 100
        # 官方历史接口不提供成交量；使用0保持OHLC缓存完整，并明确不伪造成交数据。
        for column, default in (("vol", 0.0), ("amount", 0.0), ("turnover_rate", None), ("is_st", 0)):
            if column not in data:
                data[column] = default
            else:
                data[column] = data[column].fillna(default) if default is not None else data[column]
        return data
