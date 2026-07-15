"""默认关闭的 Tushare 最后兜底，便于后续整体移除。"""

import pandas as pd

from common.tushare_token import tushare_token
from market_data.exceptions import ProviderUnavailableError


class TushareProvider:
    name = "tushare"
    supported_periods = {"D"}

    def fetch_bars(self, symbol, period, start_date, end_date):
        if period != "D":
            raise ProviderUnavailableError("Tushare 兜底仅提供日线")
        try:
            import tushare as ts

            data = ts.pro_bar(
                ts_code=symbol,
                start_date=pd.Timestamp(start_date).strftime("%Y%m%d"),
                end_date=pd.Timestamp(end_date).strftime("%Y%m%d"),
                factors=["tor", "vr"],
                api=ts.pro_api(tushare_token),
            )
        except Exception as exc:
            raise ProviderUnavailableError(f"Tushare 请求失败: {exc}") from exc
        if data is None or data.empty:
            raise ProviderUnavailableError(f"Tushare 未返回 {symbol} 日线数据")
        data = data.rename(columns={"trade_date": "trade_time", "tor": "turnover_rate"})
        data["trade_time"] = pd.to_datetime(data["trade_time"], errors="coerce")
        data["pre_close"] = data["close"].shift(-1)
        data["is_st"] = 0
        return data.sort_values("trade_time").reset_index(drop=True)

    def close(self):
        return None
