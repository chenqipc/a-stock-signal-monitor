"""Tushare免费日线最后兜底，含多Token轮换和进程级限速。"""

from collections import deque
from datetime import date
from threading import RLock
import time

import pandas as pd

from market_data.exceptions import ProviderUnavailableError


class TushareProvider:
    """仅请求120积分可用的未复权股票日线，避免调用积分接口。"""

    name = "tushare"
    supported_periods = {"D"}
    _rate_lock = RLock()
    _request_times = deque()
    _daily_request_date = date.today()
    _daily_request_count = 0
    _token_cursor = 0

    def __init__(self, tokens, requests_per_minute=45, daily_request_limit=7500):
        self.tokens = list(dict.fromkeys(str(token).strip() for token in tokens if str(token).strip()))
        if not self.tokens:
            raise ValueError("Tushare至少需要一个Token")
        self.requests_per_minute = max(1, int(requests_per_minute))
        self.daily_request_limit = max(1, int(daily_request_limit))

    @staticmethod
    def supports_symbol(symbol):
        """免费daily接口只用于A股个股，ETF和指数交给各自专用行情源。"""
        normalized = str(symbol).strip().upper()
        if normalized.startswith(("SH.", "SZ.", "BJ.")):
            exchange, code = normalized.split(".", 1)
        elif "." in normalized:
            code, exchange = normalized.split(".", 1)
        else:
            return False
        if exchange == "SH":
            return code.startswith(("6", "9"))
        if exchange == "SZ":
            return code.startswith(("000", "001", "002", "003", "300", "301"))
        return exchange == "BJ" and code.startswith(("4", "8", "92"))

    def fetch_bars(self, symbol, period, start_date, end_date):
        if period != "D":
            raise ProviderUnavailableError("Tushare兜底仅提供日线")
        token = self._acquire_token()
        try:
            import tushare as ts

            data = ts.pro_api(token).daily(
                ts_code=self._normalize_symbol(symbol),
                start_date=pd.Timestamp(start_date).strftime("%Y%m%d"),
                end_date=pd.Timestamp(end_date).strftime("%Y%m%d"),
            )
        except Exception as exc:
            raise ProviderUnavailableError(f"Tushare免费日线请求失败: {self._safe_error(exc)}") from exc
        if data is None or data.empty:
            raise ProviderUnavailableError(f"Tushare未返回 {symbol} 日线数据")
        result = data.rename(columns={"trade_date": "trade_time", "pct_chg": "pct_chg"}).copy()
        result["trade_time"] = pd.to_datetime(result["trade_time"], errors="coerce")
        for column in ["open", "close", "high", "low", "pre_close", "pct_chg", "vol", "amount"]:
            result[column] = pd.to_numeric(result.get(column), errors="coerce")
        # Tushare免费daily的成交量单位为手、成交额单位为千元，入库前统一成股和元。
        result["vol"] = result["vol"] * 100
        result["amount"] = result["amount"] * 1000
        result["turnover_rate"] = None
        result["is_st"] = 0
        return result.dropna(subset=["trade_time", "close"]).sort_values("trade_time").reset_index(drop=True)

    def _acquire_token(self):
        """两个Token共用总额度，避免误把同账号Token当成双倍配额。"""
        while True:
            wait_seconds = 0.05
            now = time.monotonic()
            with self._rate_lock:
                self._reset_daily_counter_if_needed()
                if self._daily_request_count >= self.daily_request_limit:
                    raise ProviderUnavailableError(f"Tushare今日请求已达到安全上限 {self.daily_request_limit} 次")
                while self._request_times and now - self._request_times[0] >= 60:
                    self._request_times.popleft()
                if len(self._request_times) < self.requests_per_minute:
                    token = self.tokens[self._token_cursor % len(self.tokens)]
                    type(self)._token_cursor += 1
                    self._request_times.append(now)
                    type(self)._daily_request_count += 1
                    return token
                wait_seconds = min(60, max(0.05, 60 - (now - self._request_times[0])))
            time.sleep(wait_seconds)

    @classmethod
    def _reset_daily_counter_if_needed(cls):
        today = date.today()
        if cls._daily_request_date != today:
            cls._daily_request_date = today
            cls._daily_request_count = 0

    def _safe_error(self, exc):
        message = str(exc)
        for token in self.tokens:
            message = message.replace(token, "***")
        return message

    @staticmethod
    def _normalize_symbol(symbol):
        normalized = str(symbol).strip().upper()
        if normalized.startswith(("SH.", "SZ.", "BJ.")):
            exchange, code = normalized.split(".", 1)
            return f"{code}.{exchange}"
        return normalized

    def close(self):
        return None
