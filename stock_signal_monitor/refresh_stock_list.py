"""通过免费数据源刷新 A-Stock Signal Monitor 的证券主数据。"""

from market_data.service import get_default_service


def refresh_stock_list(service=None):
    """优先使用 BaoStock，失败时自动使用东方财富，并原子更新SQLite证券主表。"""
    market_data_service = service or get_default_service()
    try:
        data = market_data_service.get_stock_list(force_refresh=True)
        database_path = market_data_service.database.database_path
        print(f"已保存 {len(data)} 条证券数据到 {database_path} 的 stock_master 表")
        return data
    finally:
        if service is None:
            market_data_service.close()


if __name__ == "__main__":
    refresh_stock_list()
