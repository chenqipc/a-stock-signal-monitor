"""BaoStock 免费行情源。"""

from datetime import datetime
from threading import RLock

import pandas as pd

from market_data.exceptions import ProviderUnavailableError


class BaoStockProvider:
    """提供日线、分钟线、交易日历和证券列表，连接在实例生命周期内复用。"""

    name = "baostock"
    supported_periods = {"D", "5min", "15min", "30min", "60min", "120min"}
    _session_lock = RLock()
    _shared_client = None
    _session_users = 0

    def __init__(self):
        self._client = None
        self._logged_in = False
        self._session_acquired = False

    def _ensure_login(self):
        """获取进程级共享会话，避免并发任务互相登录、登出。"""
        with self._session_lock:
            self._ensure_login_locked()

    def _ensure_login_locked(self):
        provider_class = type(self)
        if provider_class._shared_client is None:
            provider_class._shared_client = self._login()
        self._client = provider_class._shared_client
        self._logged_in = True
        if not self._session_acquired:
            provider_class._session_users += 1
            self._session_acquired = True

    @staticmethod
    def _login():
        try:
            import baostock as bs
        except ImportError as exc:
            raise ProviderUnavailableError("未安装 baostock") from exc
        login_result = bs.login()
        if login_result.error_code != "0":
            raise ProviderUnavailableError(f"BaoStock 登录失败: {login_result.error_msg}")
        return bs

    def close(self):
        """仅最后一个会话使用者退出时才真正登出 BaoStock。"""
        provider_class = type(self)
        with self._session_lock:
            if self._session_acquired:
                provider_class._session_users = max(0, provider_class._session_users - 1)
                self._session_acquired = False
            if provider_class._session_users == 0 and provider_class._shared_client is not None:
                provider_class._shared_client.logout()
                provider_class._shared_client = None
            self._logged_in = False
            self._client = None

    def _query_frame(self, method_name, *args):
        """串行执行 BaoStock 查询；会话失效时自动重新登录并重试一次。"""
        with self._session_lock:
            self._ensure_login_locked()
            result = getattr(self._client, method_name)(*args)
            if self._is_session_error(result):
                type(self)._shared_client = self._login()
                self._client = type(self)._shared_client
                result = getattr(self._client, method_name)(*args)
            return self._result_to_frame(result)

    @staticmethod
    def _is_session_error(result):
        error_message = str(getattr(result, "error_msg", "")).lower()
        return getattr(result, "error_code", "0") != "0" and (
            "用户未登录" in error_message or "not logged" in error_message or "not login" in error_message
        )

    @staticmethod
    def normalize_symbol(symbol):
        symbol = str(symbol).strip()
        lower_symbol = symbol.lower()
        if lower_symbol.startswith(("sh.", "sz.", "bj.")):
            return lower_symbol
        code = symbol.split(".")[0]
        suffix = symbol.split(".")[1].lower() if "." in symbol else ""
        if suffix in {"sh", "sz", "bj"}:
            return f"{suffix}.{code}"
        if code.startswith("92"):
            return f"bj.{code}"
        if code.startswith(("5", "6", "9")):
            return f"sh.{code}"
        if code.startswith(("4", "8")):
            return f"bj.{code}"
        return f"sz.{code}"

    def fetch_bars(self, symbol, period, start_date, end_date):
        if period not in self.supported_periods:
            raise ProviderUnavailableError(f"BaoStock 不支持周期: {period}")
        if not self.supports_symbol(symbol):
            raise ProviderUnavailableError(f"BaoStock 不支持证券市场: {symbol}")
        if period == "120min":
            return self._merge_60min_to_120min(self.fetch_bars(symbol, "60min", start_date, end_date))
        frequency = "d" if period == "D" else period.replace("min", "")
        if period == "D":
            fields = "date,code,open,high,low,close,preclose,volume,amount,pctChg,turn,isST"
        else:
            fields = "date,time,code,open,high,low,close,volume,amount,adjustflag"
        data = self._query_frame(
            "query_history_k_data_plus",
            self.normalize_symbol(symbol), fields, self._format_date(start_date), self._format_date(end_date), frequency, "2"
        )
        if data.empty:
            return data
        if period == "D":
            data = data.rename(
                columns={
                    "date": "trade_time",
                    "preclose": "pre_close",
                    "volume": "vol",
                    "pctChg": "pct_chg",
                    "turn": "turnover_rate",
                    "isST": "is_st",
                }
            )
        else:
            data = data.rename(columns={"time": "trade_time", "volume": "vol"})
            data["trade_time"] = data["trade_time"].str[:14]
        return self._normalize_bars(data)

    @staticmethod
    def supports_symbol(symbol):
        """BaoStock 仅支持沪深北代码，港股和海外指数不进入登录及查询流程。"""
        normalized = str(symbol).strip().upper()
        return normalized.startswith(("SH.", "SZ.", "BJ.")) or normalized.endswith((".SH", ".SZ", ".BJ"))

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
        ).drop(columns=["trade_date", "group"])

    def fetch_trade_calendar(self, start_date, end_date):
        data = self._query_frame("query_trade_dates", self._format_date(start_date), self._format_date(end_date))
        if data.empty:
            return data
        data["is_trading_day"] = pd.to_numeric(data["is_trading_day"], errors="coerce").fillna(0).astype(int)
        return data[["calendar_date", "is_trading_day"]]

    def fetch_stock_list(self):
        data = self._query_frame("query_stock_basic")
        if data.empty:
            return data
        if "type" in data.columns:
            data = data[data["type"] == "1"]
        if "status" in data.columns:
            data = data[data["status"] == "1"]
        data["symbol"] = data["code"].str.split(".").str[1]
        data["exchange"] = data["code"].str.split(".").str[0].str.upper()
        data["ts_code"] = data["symbol"] + "." + data["exchange"]
        data["name"] = data["code_name"]
        data["market"] = data["exchange"].map({"SH": "上海", "SZ": "深圳", "BJ": "北京"})
        data["list_date"] = data.get("ipoDate", "").astype(str).str.replace("-", "", regex=False)
        return data[["ts_code", "symbol", "name", "market", "list_date"]].reset_index(drop=True)

    def fetch_etf_list(self):
        """读取BaoStock证券类型5的ETF/基金清单，作为完整ETF列表的首选免费源。"""
        data = self._query_frame("query_stock_basic")
        if data.empty:
            return data
        if "type" in data.columns:
            data = data[data["type"] == "5"]
        if "status" in data.columns:
            data = data[data["status"] == "1"]
        data["symbol"] = data["code"].str.split(".").str[1]
        data["exchange"] = data["code"].str.split(".").str[0].str.upper()
        data["ts_code"] = data["symbol"] + "." + data["exchange"]
        data["name"] = data["code_name"]
        data["market"] = data["exchange"].map({"SH": "上海", "SZ": "深圳", "BJ": "北京"})
        return data[["ts_code", "symbol", "name", "market"]].reset_index(drop=True)

    @staticmethod
    def _format_date(value):
        return pd.Timestamp(value).strftime("%Y-%m-%d")

    @staticmethod
    def _result_to_frame(result):
        if result.error_code != "0":
            raise ProviderUnavailableError(f"BaoStock 查询失败: {result.error_msg}")
        rows = []
        while result.next():
            rows.append(result.get_row_data())
        return pd.DataFrame(rows, columns=result.fields)

    @staticmethod
    def _normalize_bars(data):
        data = data.copy()
        if data.empty:
            return data
        data["trade_time"] = pd.to_datetime(data["trade_time"], errors="coerce")
        numeric_columns = ["open", "close", "high", "low", "vol", "amount", "pre_close", "pct_chg", "turnover_rate", "is_st"]
        for column in numeric_columns:
            if column in data.columns:
                data[column] = pd.to_numeric(data[column], errors="coerce")
        return data.dropna(subset=["trade_time", "close"]).sort_values("trade_time").reset_index(drop=True)
