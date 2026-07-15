"""A股交易日期与盘中时段判断。"""

from datetime import time

from market_data.service import get_default_service


TRADING_SESSIONS = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))


def is_trading_time(now, service=None):
    """同时校验交易日、上午时段和下午时段。"""
    market_data_service = service or get_default_service()
    if not market_data_service.is_trading_day(now.date()):
        return False
    current_time = now.time().replace(tzinfo=None)
    return any(start <= current_time <= end for start, end in TRADING_SESSIONS)
