"""股票日线策略状态定义。"""

from enum import Enum


class StockStatus(Enum):
    NO_MATCH = "不符合条件"
    THREE_LIMIT_UP = "连续3天涨停"
    THREE_LIMIT_UP_ONLY = "最近3天涨停"
    RISING_VOLUME_INCREASE = "连续上涨且成交量放大"
    VOLUME_SURGE_WITH_PRICE_RISE = "最近3天大幅放量伴随股价上涨"
    CAPITAL_INFLOW = "资金流入明显"
    FUNDS_INFLOW_BY_VOLUME_TURNOVER = "成交量换手率放大"
    SUPPORT_LEVEL_REBOUND = "底部支撑反弹10日线"
    SUPPORT_LEVEL_REBOUND_60 = "底部支撑反弹60日均线"
    MACD_GOLDEN_CROSS = "最近3天MACD金叉"
    MACD_GOLDEN_CROSS_OVER_7 = "最近7天MACD金叉"
    DOUBLE_BOTTOM = "双底结构"
    DOUBLE_BOTTOM_NEW = "双底结构(新)"
    BREAKOUT_AFTER_CONSOLIDATION = "横盘后放量上涨"
    IS_UPWARD_TREND = "处于上涨初期"
