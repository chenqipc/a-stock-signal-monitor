"""遍历股票列表并按策略状态输出结果。"""

import time
from contextlib import ExitStack

from common.StockEnum import StockStatus
from market_data.config import RESOURCE_DIR
from market_data.service import MarketDataService

from .stock_strategy import daily_check


def scan_market(service=None):
    """使用免费行情源和SQLite增量缓存执行全市场扫描。"""
    RESOURCE_DIR.mkdir(parents=True, exist_ok=True)
    # 新版本不再生成“不符合条件”文件，启动时清理旧版本遗留结果。
    (RESOURCE_DIR / f"{StockStatus.NO_MATCH.value}.txt").unlink(missing_ok=True)
    # 清理修正“换收率”错别字前的旧文件，避免新旧结果并存。
    (RESOURCE_DIR / "成交量换收率放大.txt").unlink(missing_ok=True)
    market_data_service = service or MarketDataService()
    run_id = None
    processed_stocks = 0
    matched_stocks = 0
    error_count = 0

    try:
        # 证券列表优先从SQLite读取；数据库为空时由服务自动访问免费数据源并落库。
        stock_data = market_data_service.get_stock_list()
        run_id = market_data_service.database.start_scan_run(len(stock_data))
        with ExitStack() as stack:
            files = {
                status: stack.enter_context((RESOURCE_DIR / f"{status.value}.txt").open("w", encoding="utf-8"))
                for status in StockStatus
                if status != StockStatus.NO_MATCH
            }
            for _, row in stock_data.iterrows():
                ts_code = row["ts_code"]
                name = row["name"]
                print(f"[{ts_code}][{name}]")
                try:
                    results = daily_check(ts_code, name, market_data_service)
                except Exception as exc:
                    print(f"[{ts_code}][{name}] 获取或分析失败: {exc}")
                    market_data_service.database.save_scan_error(
                        run_id, ts_code, name, type(exc).__name__, str(exc) or repr(exc)
                    )
                    error_count += 1
                    processed_stocks += 1
                    if processed_stocks % 25 == 0:
                        market_data_service.database.update_scan_run(
                            run_id, processed_stocks, matched_stocks, error_count
                        )
                    continue
                matched_signal_types = []
                for status in results:
                    if status == StockStatus.NO_MATCH:
                        continue
                    matched_signal_types.append(status.value)
                    print(f"[{ts_code}][{name}] {status.value}")
                    files[status].write(f"{ts_code} {name}\n")
                    files[status].flush()
                if matched_signal_types:
                    matched_stocks += 1
                    market_data_service.database.save_stock_signals(
                        run_id, ts_code, name, matched_signal_types
                    )
                processed_stocks += 1
                if processed_stocks % 25 == 0:
                    market_data_service.database.update_scan_run(
                        run_id, processed_stocks, matched_stocks, error_count
                    )
                # 轻量限速仅用于保护免费服务，历史命中SQLite时不会产生网络请求。
                time.sleep(0.2)
        market_data_service.database.finish_scan_run(
            run_id, "completed", processed_stocks, matched_stocks, error_count
        )
    except BaseException as exc:
        if run_id is not None:
            market_data_service.database.finish_scan_run(
                run_id, "failed", processed_stocks, matched_stocks, error_count, str(exc)
            )
        raise
    finally:
        if service is None:
            market_data_service.close()

    for status in StockStatus:
        if status != StockStatus.NO_MATCH:
            remove_empty_file(RESOURCE_DIR / f"{status.value}.txt")
    return {
        "run_id": run_id,
        "processed_stocks": processed_stocks,
        "matched_stocks": matched_stocks,
        "error_count": error_count,
    }


def retry_scan_errors(run_id, service=None):
    """重试原扫描任务中的失败股票，并将本次重试作为独立任务持久化。"""
    market_data_service = service or MarketDataService()
    database = market_data_service.database
    scan_run = database.get_scan_run(run_id)
    if not scan_run:
        if service is None:
            market_data_service.close()
        raise ValueError(f"扫描任务不存在: {run_id}")
    errors = database.get_scan_errors(run_id, unresolved_only=True)
    if not errors:
        if service is None:
            market_data_service.close()
        raise ValueError(f"扫描任务没有可重试的失败股票: {run_id}")
    task_run_id = database.start_retry_run(run_id, len(errors))
    processed_count = 0
    resolved_count = 0
    failed_count = 0
    try:
        for item in errors:
            ts_code = item["ts_code"]
            stock_name = item["stock_name"]
            try:
                results = daily_check(ts_code, stock_name, market_data_service)
                signal_types = [status.value for status in results if status != StockStatus.NO_MATCH]
                database.save_stock_signals(run_id, ts_code, stock_name, signal_types)
                database.resolve_scan_error(run_id, ts_code)
                resolved_count += 1
            except Exception as exc:
                database.save_scan_error(
                    run_id,
                    ts_code,
                    stock_name,
                    type(exc).__name__,
                    str(exc) or repr(exc),
                    increment_retry=True,
                )
                failed_count += 1
            processed_count += 1
            # 重试股票数量通常较少，逐条落库才能让任务队列及时展示真实进度。
            database.update_scan_run(task_run_id, processed_count, resolved_count, failed_count)
            time.sleep(0.2)
        counts = database.refresh_scan_run_counts(run_id)
        database.finish_scan_run(
            task_run_id, "completed", processed_count, resolved_count, failed_count
        )
        return {
            "run_id": run_id,
            "task_run_id": task_run_id,
            "retried_count": len(errors),
            "resolved_count": resolved_count,
            "failed_count": failed_count,
            **counts,
        }
    except BaseException as exc:
        database.finish_scan_run(
            task_run_id, "failed", processed_count, resolved_count, failed_count, str(exc)
        )
        raise
    finally:
        if service is None:
            market_data_service.close()


def remove_empty_file(file_path):
    """清理没有命中结果的输出文件。"""
    if file_path.exists() and not file_path.read_text(encoding="utf-8").strip():
        file_path.unlink()
        print(f"已删除空文件: {file_path}")


if __name__ == "__main__":
    scan_market()
