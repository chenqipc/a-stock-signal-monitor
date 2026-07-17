"""行情服务与Provider的结构化类型契约。"""

from datetime import date, datetime
from typing import Optional, Protocol, Union

import pandas as pd


DateLike = Union[str, date, datetime, pd.Timestamp]


class BarProvider(Protocol):
    """所有K线Provider必须实现的最小接口。"""

    name: str
    supported_periods: set[str]

    def fetch_bars(self, symbol: str, period: str, start_date: DateLike, end_date: DateLike) -> pd.DataFrame:
        ...

    def close(self) -> None:
        ...


class DailyDataReader(Protocol):
    """策略数据准备层依赖的最小日线读取接口。"""

    def get_daily_data(
        self,
        symbol: str,
        start_date: DateLike,
        end_date: DateLike,
        force_refresh: bool = False,
        minimum_trade_time: Optional[DateLike] = None,
        refresh_latest: bool = False,
    ) -> pd.DataFrame:
        ...
