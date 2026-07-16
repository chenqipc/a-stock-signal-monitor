"""上海黄金交易所Au99.99现货黄金日线行情源。"""

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

    def __init__(self, timeout=HTTP_TIMEOUT_SECONDS):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.sge.com.cn/sjzx/mrhq",
                "X-Requested-With": "XMLHttpRequest",
            }
        )

    def close(self):
        self.session.close()

    @classmethod
    def supports_symbol(cls, symbol):
        return str(symbol).strip().upper() == cls._symbol

    def fetch_bars(self, symbol, period, start_date, end_date):
        if period != "D" or not self.supports_symbol(symbol):
            raise ProviderUnavailableError(f"上海黄金交易所不支持 {symbol} {period}")
        try:
            response = self.session.post(
                self._history_url, data={"instid": self._instrument}, timeout=self.timeout
            )
            response.raise_for_status()
            rows = (response.json() or {}).get("time") or []
        except (requests.RequestException, ValueError) as exc:
            raise ProviderUnavailableError(f"上海黄金交易所日线请求失败: {exc}") from exc
        if not rows:
            raise ProviderUnavailableError("上海黄金交易所未返回Au99.99日线数据")
        return self._parse_history(rows, start_date, end_date)

    @staticmethod
    def _parse_history(rows, start_date, end_date):
        data = pd.DataFrame(rows, columns=["trade_time", "open", "close", "low", "high"])
        data["trade_time"] = pd.to_datetime(data["trade_time"], errors="coerce")
        for column in ("open", "close", "low", "high"):
            data[column] = pd.to_numeric(data[column], errors="coerce")
        data = data.dropna(subset=["trade_time", "open", "close", "low", "high"]).sort_values("trade_time")
        data["pre_close"] = data["close"].shift(1)
        data["pct_chg"] = data["close"].pct_change(fill_method=None) * 100
        # 官方历史接口不提供成交量；使用0保持OHLC缓存完整，并明确不伪造成交数据。
        data["vol"] = 0.0
        data["amount"] = 0.0
        data["turnover_rate"] = None
        data["is_st"] = 0
        start = pd.Timestamp(start_date).normalize()
        end = pd.Timestamp(end_date).normalize()
        return data[data["trade_time"].between(start, end)].reset_index(drop=True)
