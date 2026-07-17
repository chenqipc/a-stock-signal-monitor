"""统一的行情获取、缓存与交易日历服务。"""

from .service import MarketDataService, close_default_service, get_default_service

__all__ = ["MarketDataService", "close_default_service", "get_default_service"]
