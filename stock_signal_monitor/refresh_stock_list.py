"""通过免费数据源刷新 A-Stock Signal Monitor 的证券主数据。"""

import logging

from infrastructure.logging import configure_application_logging
from market_data.service import close_default_service, get_default_service


logger = logging.getLogger(__name__)


def refresh_stock_list(service=None):
    """优先使用 BaoStock，失败时自动使用东方财富，并原子更新SQLite证券主表。"""
    configure_application_logging()
    market_data_service = service or get_default_service()
    try:
        data = market_data_service.get_stock_list(force_refresh=True)
        database_path = market_data_service.database.database_path
        logger.info("已保存 %d 条证券数据到 %s 的 stock_master 表", len(data), database_path)
        return data
    finally:
        if service is None:
            close_default_service()


if __name__ == "__main__":
    refresh_stock_list()
