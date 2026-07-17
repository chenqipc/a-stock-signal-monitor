"""ETF 均线计算、穿越识别和日志记录。"""

from datetime import datetime, timedelta

import pandas as pd

from infrastructure.logging import LoggerManager
from market_data.service import get_default_service

MA_WINDOWS = (10, 30, 60)
MINIMUM_SIGNAL_BARS = max(MA_WINDOWS) + 1


class InsufficientSignalDataError(ValueError):
    """表示K线存在但不足以同时计算当前和前一根MA60。"""


def calculate_moving_averages(data):
    """返回带MA10/MA30/MA60的副本，供调度器和Web实时任务复用。"""
    if data is None or data.empty:
        return data
    result = data.sort_values("trade_time").reset_index(drop=True).copy()
    for window in MA_WINDOWS:
        result[f"MA{window}"] = result["close"].rolling(window=window).mean()
    return result


def build_signal_snapshot(data):
    """将最后两根K线转换为可持久化的价格、均线位置与穿越状态。"""
    prepared = calculate_moving_averages(data)
    if prepared is None or prepared.empty:
        raise ValueError("分钟K线为空")
    if len(prepared) < MINIMUM_SIGNAL_BARS:
        raise InsufficientSignalDataError(f"样本不足：均线穿越判断至少需要{MINIMUM_SIGNAL_BARS}根完整K线，当前仅{len(prepared)}根")
    latest = prepared.iloc[-1]
    ma_values = {window: _optional_float(latest[f"MA{window}"]) for window in MA_WINDOWS}
    crosses = detect_ma_crosses(prepared)
    complete_ma_values = [value for value in ma_values.values() if value is not None]
    return {
        "bar_time": pd.Timestamp(latest["trade_time"]).strftime("%Y-%m-%d %H:%M:%S"),
        "close": float(latest["close"]),
        "ma_values": ma_values,
        "crosses": crosses,
        "above_all": len(complete_ma_values) == len(MA_WINDOWS) and all(float(latest["close"]) > value for value in complete_ma_values),
    }


def _optional_float(value):
    return None if pd.isna(value) else float(value)


def get_ma_data(ts_code, period="D", start_date=None, end_date=None, ts_name=None, service=None, minimum_trade_time=None):
    """从统一行情服务读取K线，并计算不同窗口的移动平均线。"""
    ts_name = ts_name or ts_code
    logger = LoggerManager.get_logger(f"{ts_code}_{ts_name}")
    start_date = start_date or (datetime.today() - timedelta(days=120)).strftime("%Y%m%d")
    end_date = end_date or datetime.today().strftime("%Y%m%d")
    market_data_service = service or get_default_service()
    try:
        if period == "D":
            data = market_data_service.get_daily_data(ts_code, start_date, end_date)
        else:
            data = market_data_service.get_minute_data(
                ts_code, period, start_date, end_date, minimum_trade_time=minimum_trade_time
            )
        if data is None or data.empty:
            return None
        return calculate_moving_averages(data)
    except Exception as exc:
        logger.exception("获取数据失败：%s", exc)
        return None


def detect_ma_crosses(data):
    """比较相邻两根K线各自的价格和均线，返回真正发生的穿越方向。"""
    crosses = {window: None for window in MA_WINDOWS}
    if data is None or data.empty or len(data) < 2:
        return crosses
    latest = data.iloc[-1]
    previous = data.iloc[-2]
    for window in MA_WINDOWS:
        column = f"MA{window}"
        current_ma = latest[column]
        previous_ma = previous[column]
        if pd.isna(current_ma) or pd.isna(previous_ma):
            continue
        if previous["close"] <= previous_ma and latest["close"] > current_ma:
            crosses[window] = "up"
        elif previous["close"] >= previous_ma and latest["close"] < current_ma:
            crosses[window] = "down"
    return crosses


def check_ma_cross(data, ts_code=None, period_name=None, ts_name=None, service=None):
    """记录均线位置和真实穿越；通知界面已移除，信号统一写入日志。"""
    if data is None or data.empty or len(data) < 2:
        return False
    ts_name = ts_name or ts_code
    logger = LoggerManager.get_logger(f"{ts_code}_{ts_name}") if ts_code else None
    latest = data.iloc[-1]
    crosses = detect_ma_crosses(data)
    for window in MA_WINDOWS:
        moving_average = latest[f"MA{window}"]
        if pd.isna(moving_average):
            continue
        position = "上方" if latest["close"] > moving_average else "下方"
        direction = crosses[window]
        if logger:
            if direction:
                action = "上穿" if direction == "up" else "下穿"
                message = f"{ts_code} ({ts_name}) 在 {period_name} 当前价格{action}了{window}周期均线"
                logger.info(message)
            else:
                logger.info("%s (%s) 在 %s 当前价格位于%d周期均线%s", ts_code, ts_name, period_name, window, position)

    valid_averages = [latest[f"MA{window}"] for window in MA_WINDOWS]
    if any(pd.isna(value) for value in valid_averages):
        return False
    return all(latest["close"] > value for value in valid_averages)


def monitor_etf(ts_code, period="D", ts_name=None, service=None):
    """兼容原有单个ETF监控入口。"""
    ts_name = ts_name or ts_code
    data = get_ma_data(ts_code, period, ts_name=ts_name, service=service)
    if data is None:
        return False
    if period != "D" and data["trade_time"].max().date() != datetime.now().date():
        return False
    period_name = "日线" if period == "D" else f"{period}线"
    return check_ma_cross(data, ts_code, period_name, ts_name, service)
