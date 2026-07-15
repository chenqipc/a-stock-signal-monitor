"""通过免费数据源刷新ETF主数据。"""

from market_data.service import get_default_service


def refresh_etf_list(service=None):
    """刷新ETF清单并原子写入SQLite，失败时由服务层回退到旧快照。"""
    market_data_service = service or get_default_service()
    try:
        data = market_data_service.get_etf_list(force_refresh=True)
        database_path = market_data_service.database.database_path
        print(f"已保存 {len(data)} 条ETF数据到 {database_path} 的 etf_master 表")
        return data
    finally:
        if service is None:
            market_data_service.close()


def main():
    refresh_etf_list()


if __name__ == "__main__":
    main()
