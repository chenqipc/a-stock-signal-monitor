"""兼容旧入口的东方财富行情客户端。"""

from market_data.providers.eastmoney_provider import EastMoneyProvider


class EastMoneyAPI(EastMoneyProvider):
    """旧名称兼容层，新代码应通过 MarketDataService 获取行情。"""
