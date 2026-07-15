"""统一的行情获取、缓存与交易日历服务。"""

from .service import MarketDataService, get_default_service

__all__ = ["MarketDataService", "get_default_service"]
