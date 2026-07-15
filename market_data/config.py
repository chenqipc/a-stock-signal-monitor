"""项目级路径和行情数据配置。"""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESOURCE_DIR = PROJECT_ROOT / "resource"
DATABASE_PATH = Path(os.getenv("MARKET_DATA_DB", RESOURCE_DIR / "market_data.db"))
ENABLE_TUSHARE_FALLBACK = os.getenv("ENABLE_TUSHARE_FALLBACK", "0") == "1"
HTTP_TIMEOUT_SECONDS = float(os.getenv("MARKET_DATA_HTTP_TIMEOUT", "10"))
PROVIDER_MAX_RETRIES = int(os.getenv("MARKET_DATA_MAX_RETRIES", "2"))
ALLOW_INSECURE_HTTP_FALLBACK = os.getenv("ALLOW_INSECURE_HTTP_FALLBACK", "0") == "1"
DAILY_CACHE_OVERLAP_BARS = max(2, int(os.getenv("DAILY_CACHE_OVERLAP_BARS", "2")))
MINUTE_CACHE_TTL_SECONDS = int(os.getenv("MINUTE_CACHE_TTL_SECONDS", "60"))
MIN_STOCK_LIST_SIZE = int(os.getenv("MIN_STOCK_LIST_SIZE", "1000"))
MIN_ETF_LIST_SIZE = int(os.getenv("MIN_ETF_LIST_SIZE", "100"))
