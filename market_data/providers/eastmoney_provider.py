"""东方财富免费行情源，包含超时、重试和响应校验。"""

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from market_data.config import ALLOW_INSECURE_HTTP_FALLBACK, HTTP_TIMEOUT_SECONDS, PROVIDER_MAX_RETRIES
from market_data.exceptions import ProviderUnavailableError


class EastMoneyProvider:
    """获取沪深北证券日线和分钟线，并作为 BaoStock 的免费兜底。"""

    name = "eastmoney"
    supported_periods = {"D", "1min", "5min", "15min", "30min", "60min", "120min"}
    _period_map = {"D": "101", "1min": "1", "5min": "5", "15min": "15", "30min": "30", "60min": "60"}

    def __init__(self, timeout=HTTP_TIMEOUT_SECONDS):
        self.timeout = timeout
        self.session = self._build_session(trust_env=True)
        self.direct_session = self._build_session(trust_env=False)

    @staticmethod
    def _build_session(trust_env):
        """分别构建环境代理会话和直连会话，代理失效时可安全降级。"""
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
        session.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
        return session

    def close(self):
        self.session.close()
        self.direct_session.close()

    @staticmethod
    def normalize_symbol(symbol):
        symbol = str(symbol).strip()
        if symbol.lower().startswith(("sh.", "sz.", "bj.")):
            prefix, code = symbol.split(".", 1)
            market_id = {"sh": "1", "sz": "0", "bj": "0"}[prefix.lower()]
            return code, market_id
        parts = symbol.split(".")
        code = parts[0]
        if len(parts) > 1:
            suffix = parts[1].upper()
            if suffix == "HK":
                market_id = "100"
            else:
                market_id = "1" if suffix == "SH" else "0"
        else:
            market_id = "1" if code.startswith(("5", "6", "9")) else "0"
        return code, market_id

    def get_market_id(self, symbol, security_type="ETF"):
        code, market_id = self.normalize_symbol(symbol)
        return market_id, f"{market_id}.{code}"

    def fetch_bars(self, symbol, period, start_date, end_date):
        if period not in self.supported_periods:
            raise ProviderUnavailableError(f"东方财富不支持周期: {period}")
        if not self.supports_symbol(symbol):
            raise ProviderUnavailableError(f"东方财富不支持证券市场: {symbol}")
        if period == "120min":
            return self._merge_60min_to_120min(self.fetch_bars(symbol, "60min", start_date, end_date))
        code, market_id = self.normalize_symbol(symbol)
        data = self._request_klines(code, market_id, period, start_date, end_date)
        if data.empty:
            alternate_market = "0" if market_id == "1" else "1"
            data = self._request_klines(code, alternate_market, period, start_date, end_date)
        if data.empty:
            raise ProviderUnavailableError(f"东方财富未返回 {symbol} {period} 数据")
        return data

    @staticmethod
    def supports_symbol(symbol):
        normalized = str(symbol).strip().upper()
        return normalized.startswith(("SH.", "SZ.", "BJ.")) or normalized.endswith((".SH", ".SZ", ".BJ", ".HK"))

    def get_daily_data(self, symbol, start_date=None, end_date=None):
        return self.fetch_bars(symbol, "D", start_date or "20200101", end_date or "20500101")

    def get_minute_data(self, symbol, period, start_date=None, end_date=None):
        return self.fetch_bars(symbol, period, start_date or "20200101", end_date or "20500101")

    def merge_60min_to_120min(self, symbol, start_date=None, end_date=None):
        return self.fetch_bars(symbol, "120min", start_date or "20200101", end_date or "20500101")

    def _request_klines(self, code, market_id, period, start_date, end_date):
        urls = [
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        ]
        if ALLOW_INSECURE_HTTP_FALLBACK:
            urls.append("http://push2his.eastmoney.com/api/qt/stock/kline/get")
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": self._period_map[period],
            "fqt": "1",
            "secid": f"{market_id}.{code}",
            "beg": pd.Timestamp(start_date).strftime("%Y%m%d"),
            "end": pd.Timestamp(end_date).strftime("%Y%m%d"),
        }
        payload = self._get_json(urls, params, "K线")
        klines = (payload.get("data") or {}).get("klines") or []
        if not klines:
            return pd.DataFrame()
        rows = [line.split(",") for line in klines]
        columns = ["trade_time", "open", "close", "high", "low", "vol", "amount", "amplitude", "pct_chg", "price_change", "turnover_rate"]
        data = pd.DataFrame(rows, columns=columns)
        data["trade_time"] = pd.to_datetime(data["trade_time"], errors="coerce")
        for column in columns[1:]:
            data[column] = pd.to_numeric(data[column], errors="coerce")
        data["pre_close"] = data["close"].shift(1)
        data["is_st"] = 0
        return data.dropna(subset=["trade_time", "close"]).sort_values("trade_time").reset_index(drop=True)

    @staticmethod
    def _merge_60min_to_120min(data):
        if data is None or data.empty:
            return data
        result = data.copy().sort_values("trade_time")
        result["trade_date"] = result["trade_time"].dt.date
        result["group"] = result.groupby("trade_date").cumcount() // 2
        result = result.groupby(["trade_date", "group"], as_index=False).agg(
            trade_time=("trade_time", "max"),
            open=("open", "first"),
            close=("close", "last"),
            high=("high", "max"),
            low=("low", "min"),
            vol=("vol", "sum"),
            amount=("amount", "sum"),
            pre_close=("pre_close", "first"),
            pct_chg=("pct_chg", "sum"),
            turnover_rate=("turnover_rate", "sum"),
            is_st=("is_st", "max"),
        )
        return result.drop(columns=["trade_date", "group"])

    def fetch_stock_list(self):
        urls = ["https://push2.eastmoney.com/api/qt/clist/get"]
        if ALLOW_INSECURE_HTTP_FALLBACK:
            urls.append("http://push2.eastmoney.com/api/qt/clist/get")
        params = {
            "pn": "1",
            "pz": "10000",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f12,f13,f14",
        }
        items = (self._get_json(urls, params, "证券列表").get("data") or {}).get("diff") or []
        rows = []
        for item in items:
            code = str(item.get("f12", ""))
            exchange = "SH" if str(item.get("f13")) == "1" else "SZ"
            rows.append({"ts_code": f"{code}.{exchange}", "symbol": code, "name": item.get("f14", ""), "market": exchange, "list_date": ""})
        return pd.DataFrame(rows)

    def fetch_etf_list(self):
        """读取沪深交易所ETF清单，作为ETF主数据的免费来源。"""
        urls = ["https://push2.eastmoney.com/api/qt/clist/get"]
        if ALLOW_INSECURE_HTTP_FALLBACK:
            urls.append("http://push2.eastmoney.com/api/qt/clist/get")
        params = {
            "pn": "1",
            "pz": "10000",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
            "fields": "f12,f13,f14",
        }
        items = (self._get_json(urls, params, "ETF列表").get("data") or {}).get("diff") or []
        rows = []
        for item in items:
            code = str(item.get("f12", ""))
            exchange = "SH" if str(item.get("f13")) == "1" else "SZ"
            market = "上海" if exchange == "SH" else "深圳"
            rows.append({"ts_code": f"{code}.{exchange}", "symbol": code, "name": item.get("f14", ""), "market": market})
        return pd.DataFrame(rows)

    def _get_json(self, urls, params, description):
        errors = []
        for url in urls:
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.ProxyError as exc:
                errors.append(f"{url.split(':', 1)[0]}代理: {exc}")
                try:
                    # 环境代理失效不应拖垮免费兜底源，仅在代理异常时尝试直连。
                    response = self.direct_session.get(url, params=params, timeout=self.timeout)
                    response.raise_for_status()
                    return response.json()
                except (requests.RequestException, ValueError) as direct_exc:
                    errors.append(f"{url.split(':', 1)[0]}直连: {direct_exc}")
            except (requests.RequestException, ValueError) as exc:
                errors.append(f"{url.split(':', 1)[0]}: {exc}")
        raise ProviderUnavailableError(f"东方财富{description}请求失败: {'; '.join(errors)}")
