"""行情服务异常定义。"""


class MarketDataError(RuntimeError):
    """行情数据获取或转换失败。"""


class ProviderUnavailableError(MarketDataError):
    """当前数据源不可用。"""
