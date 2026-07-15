"""按A股K线收盘时间运行的ETF监控调度器。"""

import time
from datetime import datetime, timedelta

import schedule

from common.log_utils import LoggerManager
from market_data.service import get_default_service
from market_data.trading_calendar import is_trading_time as calendar_is_trading_time

from .ma_monitor import check_ma_cross, get_ma_data
from .watchlist import DEFAULT_WATCHLIST


# 每个任务在K线收盘10秒后执行，避免把尚未完成的K线用于穿越判断。
BAR_CLOSE_TIMES = {
    "15min": ["09:45:10", "10:00:10", "10:15:10", "10:30:10", "10:45:10", "11:00:10", "11:15:10", "11:30:10",
              "13:15:10", "13:30:10", "13:45:10", "14:00:10", "14:15:10", "14:30:10", "14:45:10", "15:00:10"],
    "30min": ["10:00:10", "10:30:10", "11:00:10", "11:30:10", "13:30:10", "14:00:10", "14:30:10", "15:00:10"],
    "60min": ["10:30:10", "11:30:10", "14:00:10", "15:00:10"],
    "120min": ["11:30:10", "15:00:10"],
}


def monitor_symbol(symbol_code, symbol_name=None, period=None, service=None):
    """监控一个证券的指定周期，行情源由统一服务自动降级。"""
    symbol_name = symbol_name or symbol_code
    periods = [period] if period else list(BAR_CLOSE_TIMES)
    logger = LoggerManager.get_logger(f"{symbol_code}_{symbol_name}")
    market_data_service = service or get_default_service()
    logger.info("========== %s 开始监控 %s (%s) %s ==========", datetime.now(), symbol_code, symbol_name, period or "所有周期")
    start_date = (datetime.today() - timedelta(days=180)).strftime("%Y%m%d")
    end_date = datetime.today().strftime("%Y%m%d")

    for current_period in periods:
        expected_bar_time = latest_expected_bar_time(current_period, datetime.now())
        if expected_bar_time is None:
            logger.info("%s 周期当天尚无已完成K线，跳过本次分析", current_period)
            continue
        data = get_ma_data(
            symbol_code,
            current_period,
            start_date,
            end_date,
            symbol_name,
            market_data_service,
            expected_bar_time,
        )
        if data is None or data.empty:
            logger.warning("无法获取 %s (%s) 的 %s 数据", symbol_code, symbol_name, current_period)
            continue
        source = data.attrs.get("source", "unknown")
        if expected_bar_time and data["trade_time"].max() < expected_bar_time:
            logger.warning(
                "%s 周期最新K线为%s，早于预期的%s，来源=%s，本次不分析",
                current_period,
                data["trade_time"].max(),
                expected_bar_time,
                source,
            )
            continue
        logger.info("%s 周期获取到 %d 条数据，来源=%s", current_period, len(data), source)
        period_name = f"{current_period}线"
        is_above_ma = check_ma_cross(data, symbol_code, period_name, symbol_name, market_data_service)
        status = "上方" if is_above_ma else "下方或数据不足"
        logger.info("%s (%s) 在 %s 当前价格位于三条均线%s", symbol_code, symbol_name, period_name, status)
    logger.info("========== 监控完成 ==========")


def monitor_symbols_for_period(symbols_to_monitor, period, service=None):
    """同一周期按证券顺序执行，避免多个任务在同一秒并发请求行情源。"""
    market_data_service = service or get_default_service()
    if not market_data_service.is_trading_day(datetime.now().date()):
        LoggerManager.get_logger("main").info("当前不是交易日，跳过 %s 周期批量监控", period)
        return
    for symbol_info in symbols_to_monitor:
        symbol_code, symbol_name = _parse_symbol_info(symbol_info)
        monitor_symbol(symbol_code, symbol_name, period, market_data_service)


def setup_monitoring_tasks(symbols_to_monitor, service=None):
    """按真实K线收盘点注册批量任务，不再使用容易误解的分钟偏移。"""
    main_logger = LoggerManager.get_logger("main")
    schedule.clear("market-monitor")
    for period, close_times in BAR_CLOSE_TIMES.items():
        for close_time in close_times:
            schedule.every().day.at(close_time).do(
                monitor_symbols_for_period, symbols_to_monitor, period, service
            ).tag("market-monitor")
        main_logger.info("已设置 %s 周期监控，共%d个K线收盘时点", period, len(close_times))
    main_logger.info("所有监控任务已设置，等待K线收盘")


def scheduled_monitor(symbol_code, symbol_name=None, period=None, service=None):
    """保留旧调用入口，交易时段内执行单证券监控。"""
    if not is_trading_time(service=service):
        LoggerManager.get_logger("main").info("当前不是交易时间，跳过 %s (%s) %s", symbol_code, symbol_name, period)
        return
    monitor_symbol(symbol_code, symbol_name, period, service)


def is_trading_time(now=None, service=None):
    return calendar_is_trading_time(now or datetime.now(), service)


def start_scheduler():
    main_logger = LoggerManager.get_logger("main")
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        main_logger.info("所有监控已停止")


def _parse_symbol_info(symbol_info):
    if isinstance(symbol_info, (list, tuple)) and len(symbol_info) >= 2:
        return symbol_info[0], symbol_info[1]
    return symbol_info, symbol_info


def latest_expected_bar_time(period, now):
    """根据调度表计算当前时刻之前最近一根应当完成的K线。"""
    candidates = []
    for scheduled_time in BAR_CLOSE_TIMES.get(period, []):
        parsed_time = datetime.strptime(scheduled_time, "%H:%M:%S") - timedelta(seconds=10)
        candidate = now.replace(hour=parsed_time.hour, minute=parsed_time.minute, second=0, microsecond=0)
        if candidate <= now:
            candidates.append(candidate)
    return max(candidates) if candidates else None


def main():
    """启动 A-Stock Signal Monitor 的ETF分钟信号监控。"""
    symbols = [[item["code"], item["name"]] for item in DEFAULT_WATCHLIST]
    default_service = get_default_service()
    try:
        setup_monitoring_tasks(symbols, default_service)
        start_scheduler()
    finally:
        default_service.close()


if __name__ == "__main__":
    main()
