"""ETF 均线计算、穿越识别和去重通知。"""

from datetime import datetime, timedelta

import pandas as pd

from common.log_utils import LoggerManager
from market_data.service import get_default_service

from .gui_utils import NotificationManager


MA_WINDOWS = (10, 30, 60)
notify = NotificationManager()


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
        data = data.sort_values("trade_time").reset_index(drop=True)
        for window in MA_WINDOWS:
            data[f"MA{window}"] = data["close"].rolling(window=window).mean()
        return data
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
    """记录均线位置，只对新K线上的真实穿越发送一次通知。"""
    if data is None or data.empty or len(data) < 2:
        return False
    ts_name = ts_name or ts_code
    logger = LoggerManager.get_logger(f"{ts_code}_{ts_name}") if ts_code else None
    latest = data.iloc[-1]
    crosses = detect_ma_crosses(data)
    should_notify_period = period_name in {"30min线", "60min线", "120min线"}
    market_data_service = service or get_default_service()

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
                notification_key = f"{ts_code}:{period_name}:MA{window}:{direction}"
                trade_time = latest["trade_time"]
                if should_notify_period and market_data_service.database.should_send_notification(
                    notification_key, trade_time, direction
                ):
                    notify.show_notification(f"{ts_code} ({ts_name})", message, 5)
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
