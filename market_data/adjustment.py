"""识别并修复除权、分红、拆分等事件造成的K线价格断层。"""

import logging
from dataclasses import dataclass

import pandas as pd


logger = logging.getLogger(__name__)
PRICE_COLUMNS = ("open", "high", "low", "close", "pre_close")
MIN_RELATIVE_GAP = 0.001
MIN_ABSOLUTE_GAP = 0.0005
SPLIT_VOLUME_FACTOR_THRESHOLD = 0.10
MINUTE_SPLIT_FACTORS = (0.1, 0.2, 0.25, 1 / 3, 0.5, 2.0, 3.0, 4.0, 5.0, 10.0)
MINUTE_SPLIT_FACTOR_TOLERANCE = 0.12


@dataclass(frozen=True)
class DailyAdjustmentEvent:
    """一次价格口径切换；factor用于把生效日前的价格换算到新口径。"""

    effective_time: pd.Timestamp
    factor: float
    volume_adjusted: bool


@dataclass(frozen=True)
class MinuteAdjustmentEvent:
    """分钟线跨交易日发生的大比例价格口径切换。"""

    effective_time: pd.Timestamp
    factor: float
    volume_adjusted: bool = True


def repair_daily_price_continuity(data):
    """利用官方前收盘价和涨跌幅，将历史OHLC转换成连续的前复权口径。"""
    if data is None or data.empty or not {"trade_time", "close"}.issubset(data.columns):
        return data.copy() if data is not None else pd.DataFrame(), []
    repaired = data.sort_values("trade_time").drop_duplicates("trade_time", keep="last").reset_index(drop=True).copy()
    for column in (*PRICE_COLUMNS, "pct_chg", "vol"):
        if column in repaired.columns:
            repaired[column] = pd.to_numeric(repaired[column], errors="coerce")
    events = []
    for position in range(1, len(repaired)):
        previous_close = repaired.at[position - 1, "close"]
        effective_pre_close = _effective_pre_close(repaired.iloc[position])
        if not _valid_positive(previous_close) or not _valid_positive(effective_pre_close):
            continue
        factor = float(effective_pre_close / previous_close)
        if not _is_adjustment_factor(factor, previous_close, effective_pre_close):
            continue
        repaired.at[position, "pre_close"] = effective_pre_close
        prior_rows = repaired.index < position
        for column in PRICE_COLUMNS:
            if column in repaired.columns:
                repaired.loc[prior_rows, column] = repaired.loc[prior_rows, column] * factor
        volume_adjusted = abs(factor - 1.0) >= SPLIT_VOLUME_FACTOR_THRESHOLD
        if volume_adjusted and "vol" in repaired.columns:
            repaired.loc[prior_rows, "vol"] = repaired.loc[prior_rows, "vol"] / factor
        events.append(DailyAdjustmentEvent(pd.Timestamp(repaired.at[position, "trade_time"]), factor, volume_adjusted))
    return repaired, events


def repair_cached_daily_prices(database, symbol, start_date=None, end_date=None):
    """修复SQLite中的指定日线窗口并返回连续数据；无断层时不产生写操作。"""
    data = database.load_klines(symbol, "D", start_date, end_date)
    repaired, events = repair_daily_price_continuity(data)
    if not events:
        return repaired, events
    database.update_daily_kline_adjustments(symbol, repaired)
    for event in events:
        logger.info(
            "修复 %s 日线复权断层: 生效日=%s, 历史价格因子=%.8f, 调整历史成交量=%s",
            symbol,
            event.effective_time.strftime("%Y-%m-%d"),
            event.factor,
            event.volume_adjusted,
        )
    return repaired, events


def repair_minute_price_continuity(data):
    """识别1:2、1:3、1:4等明显拆分，将生效日前分钟线转换为当前价格口径。"""
    if data is None or data.empty or not {"trade_time", "open", "close"}.issubset(data.columns):
        return data.copy() if data is not None else pd.DataFrame(), []
    repaired = data.sort_values("trade_time").drop_duplicates("trade_time", keep="last").reset_index(drop=True).copy()
    for column in (*PRICE_COLUMNS, "pct_chg", "vol"):
        if column in repaired.columns:
            repaired[column] = pd.to_numeric(repaired[column], errors="coerce")
    events = []
    for position in range(1, len(repaired)):
        previous_time = pd.Timestamp(repaired.at[position - 1, "trade_time"])
        current_time = pd.Timestamp(repaired.at[position, "trade_time"])
        if previous_time.date() == current_time.date():
            continue
        previous_close = _number(repaired.at[position - 1, "close"])
        current_open = _number(repaired.at[position, "open"])
        if not _valid_positive(previous_close) or not _valid_positive(current_open):
            continue
        factor = _nearest_minute_split_factor(current_open / previous_close)
        if factor is None:
            continue
        prior_rows = repaired.index < position
        for column in PRICE_COLUMNS:
            if column in repaired.columns:
                repaired.loc[prior_rows, column] = repaired.loc[prior_rows, column] * factor
        if "vol" in repaired.columns:
            repaired.loc[prior_rows, "vol"] = repaired.loc[prior_rows, "vol"] / factor
        if "pre_close" in repaired.columns:
            repaired.at[position, "pre_close"] = repaired.at[position - 1, "close"]
        if "pct_chg" in repaired.columns and _valid_positive(repaired.at[position, "pre_close"]):
            repaired.at[position, "pct_chg"] = (repaired.at[position, "close"] / repaired.at[position, "pre_close"] - 1) * 100
        events.append(MinuteAdjustmentEvent(current_time, factor))
    return repaired, events


def repair_cached_minute_prices(database, symbol, period, start_date=None, end_date=None):
    """修复SQLite中的分钟价格断层，并清空依赖旧价格计算的均线。"""
    data = database.load_klines(symbol, period, start_date, end_date)
    repaired, events = repair_minute_price_continuity(data)
    if not events:
        return repaired, events
    database.update_kline_adjustments(symbol, period, repaired)
    for event in events:
        logger.info(
            "修复 %s %s 分钟线拆分断层: 生效时间=%s, 历史价格因子=%.8f",
            symbol,
            period,
            event.effective_time.strftime("%Y-%m-%d %H:%M"),
            event.factor,
        )
    return repaired, events


def _effective_pre_close(row):
    """优先采用与官方涨跌幅一致的前收盘价，兼容部分行情源用昨日收盘直接填充。"""
    close = _number(row.get("close"))
    reported_pre_close = _number(row.get("pre_close"))
    pct_change = _number(row.get("pct_chg"))
    if _valid_positive(reported_pre_close):
        if pct_change is None or close is None:
            return reported_pre_close
        reported_change = (close / reported_pre_close - 1.0) * 100.0
        tolerance = max(0.08, abs(pct_change) * 0.02)
        if abs(reported_change - pct_change) <= tolerance:
            return reported_pre_close
    denominator = 1.0 + pct_change / 100.0 if pct_change is not None else None
    if close is not None and denominator is not None and denominator > 0:
        return close / denominator
    return reported_pre_close


def _is_adjustment_factor(factor, previous_close, effective_pre_close):
    return (
        0.05 <= factor <= 20.0
        and abs(factor - 1.0) >= MIN_RELATIVE_GAP
        and abs(previous_close - effective_pre_close) >= MIN_ABSOLUTE_GAP
    )


def _nearest_minute_split_factor(observed_factor):
    """只接受远超正常涨跌幅的常见拆分比例，避免把普通隔夜跳空误判为复权事件。"""
    if observed_factor <= 0:
        return None
    nearest = min(MINUTE_SPLIT_FACTORS, key=lambda candidate: abs(observed_factor - candidate) / candidate)
    relative_error = abs(observed_factor - nearest) / nearest
    return nearest if relative_error <= MINUTE_SPLIT_FACTOR_TOLERANCE else None


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _valid_positive(value):
    return value is not None and pd.notna(value) and float(value) > 0
