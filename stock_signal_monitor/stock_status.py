"""股票日线策略状态定义。"""

from enum import Enum


class StockStatus(Enum):
    NO_MATCH = "不符合条件"
    LIMIT_UP_STREAK = "连续涨停"
    RISING_VOLUME_INCREASE = "连续上涨且成交量递增"
    VOLUME_SURGE_WITH_PRICE_RISE = "最近3天大幅放量伴随股价上涨"
    CAPITAL_INFLOW = "连续上涨且成交额放大"
    FUNDS_INFLOW_BY_VOLUME_TURNOVER = "成交量换手率放大"
    SUPPORT_LEVEL_REBOUND = "10日均线支撑反弹"
    SUPPORT_LEVEL_REBOUND_60 = "放量突破60日均线"
    MACD_GOLDEN_CROSS = "MACD金叉（近3日）"
    DOUBLE_BOTTOM = "双底突破确认"
    BREAKOUT_AFTER_CONSOLIDATION = "横盘后放量上涨"
    IS_UPWARD_TREND = "上涨初期评分"


# 公共指标采用通行技术分析定义；其余指标保留为项目自定义策略。
PUBLIC_DAILY_STATUSES = frozenset({StockStatus.MACD_GOLDEN_CROSS, StockStatus.DOUBLE_BOTTOM})


def daily_strategy_statuses(category=""):
    """按页面分类返回日线指标，并统一排除内部的未命中状态。"""
    statuses = tuple(status for status in StockStatus if status != StockStatus.NO_MATCH)
    if category == "public":
        return tuple(status for status in statuses if status in PUBLIC_DAILY_STATUSES)
    if category == "custom":
        return tuple(status for status in statuses if status not in PUBLIC_DAILY_STATUSES)
    return statuses


def daily_strategy_category(status):
    """返回指标所属页面分类，供Web目录和结果查询共享。"""
    return "public" if status in PUBLIC_DAILY_STATUSES else "custom"
