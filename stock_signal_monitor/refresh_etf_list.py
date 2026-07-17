"""通过免费数据源刷新ETF主数据。"""

import logging

from infrastructure.logging import configure_application_logging
from market_data.service import close_default_service, get_default_service


logger = logging.getLogger(__name__)


def refresh_etf_list(service=None):
    """刷新ETF清单并原子写入SQLite，失败时由服务层回退到旧快照。"""
    configure_application_logging()
    market_data_service = service or get_default_service()
    try:
        data = market_data_service.get_etf_list(force_refresh=True)
        database_path = market_data_service.database.database_path
        logger.info("已保存 %d 条ETF数据到 %s 的 etf_master 表", len(data), database_path)
        return data
    finally:
        if service is None:
            close_default_service()


def main():
    refresh_etf_list()


if __name__ == "__main__":
    main()
