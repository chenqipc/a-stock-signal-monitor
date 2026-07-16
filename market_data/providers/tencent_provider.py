"""腾讯财经A股日线行情免费兜底。"""

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from market_data.config import HTTP_TIMEOUT_SECONDS, PROVIDER_MAX_RETRIES
from market_data.exceptions import ProviderUnavailableError


class TencentProvider:
    """读取腾讯前复权日线，并用实时快照补齐当前交易日。"""

    name = "tencent"
    supported_periods = {"D"}
    _history_url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    _quote_url = "https://qt.gtimg.cn/q="

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
        session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        return session

    def close(self):
        self.session.close()
        self.direct_session.close()

    @staticmethod
    def supports_symbol(symbol):
        normalized = str(symbol).strip().upper()
        return normalized.startswith(("SH.", "SZ.")) or normalized.endswith((".SH", ".SZ"))

    def fetch_bars(self, symbol, period, start_date, end_date):
        if period != "D":
            raise ProviderUnavailableError(f"腾讯财经不支持周期: {period}")
        code = self._normalize_symbol(symbol)
        params = {
            "param": f"{code},day,{pd.Timestamp(start_date):%Y-%m-%d},{pd.Timestamp(end_date):%Y-%m-%d},1023,qfq",
        }
        payload = self._request_json(self._history_url, params, "历史K线")
        security_data = payload.get("data", {}).get(code, {})
        rows = security_data.get("qfqday") or security_data.get("day") or []
        data = self._history_frame(rows)
        try:
            realtime = self._fetch_realtime_daily(code)
        except ProviderUnavailableError:
            realtime = None
        if realtime:
            data = pd.concat([data, pd.DataFrame([realtime])], ignore_index=True)
        if data.empty:
            raise ProviderUnavailableError(f"腾讯财经未返回 {symbol} 日线数据")
        data["trade_time"] = pd.to_datetime(data["trade_time"], errors="coerce", format="mixed")
        for column in ["open", "close", "high", "low", "vol", "amount", "pre_close", "pct_chg", "turnover_rate"]:
            if column not in data:
                data[column] = None
            data[column] = pd.to_numeric(data[column], errors="coerce")
        data = data.dropna(subset=["trade_time", "close"]).sort_values("trade_time")
        data = data.drop_duplicates("trade_time", keep="last").reset_index(drop=True)
        calculated_pre_close = data["close"].shift(1)
        data["pre_close"] = data["pre_close"].fillna(calculated_pre_close)
        calculated_pct_chg = (data["close"] - data["pre_close"]) / data["pre_close"] * 100
        data["pct_chg"] = data["pct_chg"].fillna(calculated_pct_chg)
        data["is_st"] = 0
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date) + pd.Timedelta(1, unit="D")
        return data[(data["trade_time"] >= start) & (data["trade_time"] < end)].reset_index(drop=True)

    @staticmethod
    def _history_frame(rows):
        records = []
        for row in rows:
            if len(row) < 6:
                continue
            records.append({
                "trade_time": row[0], "open": row[1], "close": row[2], "high": row[3], "low": row[4],
                "vol": pd.to_numeric(row[5], errors="coerce") * 100, "amount": None,
            })
        return pd.DataFrame(records)

    def _fetch_realtime_daily(self, code):
        response = self._request_text(f"{self._quote_url}{code}", None, "实时行情")
        text = response.content.decode("gb18030", errors="replace")
        start = text.find('="')
        end = text.rfind('"')
        fields = text[start + 2:end].split("~") if start >= 0 and end > start else []
        if len(fields) < 39 or not fields[30] or pd.to_numeric(fields[3], errors="coerce") <= 0:
            return None
        return {
            "trade_time": fields[30][:8], "open": fields[5], "close": fields[3], "high": fields[33], "low": fields[34],
            "vol": pd.to_numeric(fields[36] or fields[6], errors="coerce") * 100,
            "amount": pd.to_numeric(fields[37], errors="coerce") * 10000,
            "pre_close": fields[4], "pct_chg": fields[32], "turnover_rate": fields[38],
        }

    def _request_json(self, url, params, label):
        response = self._request_text(url, params, label)
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderUnavailableError(f"腾讯财经{label}响应不是有效JSON: {exc}") from exc

    def _request_text(self, url, params, label):
        errors = []
        for route, session in (("代理", self.session), ("直连", self.direct_session)):
            try:
                response = session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                errors.append(f"{route}: {exc}")
        raise ProviderUnavailableError(f"腾讯财经{label}请求失败: {'; '.join(errors)}")

    @staticmethod
    def _normalize_symbol(symbol):
        normalized = str(symbol).strip().lower()
        if normalized.startswith(("sh.", "sz.")):
            prefix, code = normalized.split(".", 1)
            return f"{prefix}{code}"
        parts = normalized.split(".")
        code = parts[0]
        prefix = parts[1] if len(parts) > 1 else ("sh" if code.startswith(("5", "6", "9")) else "sz")
        return f"{prefix}{code}"
