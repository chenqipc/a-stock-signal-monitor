"""遍历股票列表并按策略状态输出结果。"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Optional, TextIO

import pandas as pd

from infrastructure.logging import configure_application_logging
from market_data.config import RESOURCE_DIR, SCAN_CACHED_WORKERS
from market_data.service import MarketDataService

from .stock_status import StockStatus
from .stock_strategy import build_daily_signal_details, daily_strategy_window, evaluate_daily_strategies


logger = logging.getLogger(__name__)

DEPRECATED_SIGNAL_LABELS = (
    "连续3天涨停",
    "最近3天涨停",
    "连续上涨且成交量放大",
    "资金流入明显",
    "底部支撑反弹10日线",
    "底部支撑反弹60日均线",
    "最近3天MACD金叉",
    "最近7天MACD金叉",
    "双底结构",
    "双底结构(新)",
    "处于上涨初期",
)


def scan_market(service=None, control=None, asset_type=None):
    """使用免费行情源和SQLite增量缓存扫描全部标的或指定资产类型。"""
    configure_application_logging()
    scan_scope = normalize_scan_scope(asset_type)
    RESOURCE_DIR.mkdir(parents=True, exist_ok=True)
    # 新版本不再生成“不符合条件”文件，启动时清理旧版本遗留结果。
    (RESOURCE_DIR / f"{StockStatus.NO_MATCH.value}.txt").unlink(missing_ok=True)
    # 清理修正“换收率”错别字前的旧文件，避免新旧结果并存。
    (RESOURCE_DIR / "成交量换收率放大.txt").unlink(missing_ok=True)
    for deprecated_label in DEPRECATED_SIGNAL_LABELS:
        (RESOURCE_DIR / f"{deprecated_label}.txt").unlink(missing_ok=True)
    market_data_service = service or MarketDataService()
    run_id = None
    processed_stocks = 0
    matched_stocks = 0
    error_count = 0

    try:
        # 指定范围时只读取对应主列表，避免股票/ETF单独扫描仍触发另一类资产的数据准备。
        instrument_rows = load_scan_rows(market_data_service, scan_scope)
        run_id = market_data_service.database.start_scan_run(len(instrument_rows), scan_scope=scan_scope)
        if control:
            control.set_run_id(run_id)
        start_date, end_date = daily_strategy_window()
        with ExitStack() as stack:
            files = {
                status: stack.enter_context((RESOURCE_DIR / f"{status.value}.txt").open("w", encoding="utf-8"))
                for status in StockStatus
                if status != StockStatus.NO_MATCH
            }
            context = ScanContext(market_data_service, run_id, files, control)
            for result in stream_scan_results(instrument_rows, market_data_service, start_date, end_date, control, run_id):
                processed_stocks, matched_stocks, error_count = handle_scan_result(
                    context, result, processed_stocks, matched_stocks, error_count
                )
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
        "scan_scope": scan_scope,
        "processed_stocks": processed_stocks,
        "matched_stocks": matched_stocks,
        "error_count": error_count,
    }


def normalize_scan_scope(asset_type):
    value = str(asset_type or "all").strip().lower()
    if value not in {"all", "stock", "etf"}:
        raise ValueError(f"不支持的扫描范围: {asset_type}")
    return value


def load_scan_rows(service, scan_scope):
    """股票和ETF主列表均优先从SQLite读取，按任务范围构造策略流水线输入。"""
    rows = []
    if scan_scope in {"all", "stock"}:
        rows.extend(build_scan_rows(service.get_stock_list(), "stock"))
    if scan_scope in {"all", "etf"}:
        rows.extend(build_scan_rows(service.get_etf_list(), "etf"))
    return rows


@dataclass
class ScanContext:
    """扫描结果持久化所需上下文。"""

    service: MarketDataService
    run_id: int
    files: dict[StockStatus, TextIO]
    control: Optional[object] = None


def build_scan_rows(data: pd.DataFrame, asset_type: str) -> list[dict]:
    """将股票和ETF主数据统一成带资产类型的扫描行，供同一条缓存与策略流水线处理。"""
    return [{**row.to_dict(), "asset_type": asset_type} for _, row in data.iterrows()]


def stream_scan_results(rows, service, start_date, end_date, control=None, run_id=None):
    if not rows:
        return []
    max_workers = min(SCAN_CACHED_WORKERS, len(rows))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cached-scan") as executor:
        pending = set()
        max_pending = max_workers * 4
        for row in rows:
            wait_if_paused(control, service.database, run_id)
            if can_compute_from_database(row, service, start_date, end_date):
                pending.add(executor.submit(scan_cached_row, row, service.database, start_date, end_date))
            else:
                error = prepare_daily_cache(row, service, start_date, end_date)
                if error:
                    yield {
                        "ts_code": row["ts_code"],
                        "name": row["name"],
                        "asset_type": row.get("asset_type", "stock"),
                        "results": [],
                        "details": {},
                        "error": error,
                    }
                else:
                    pending.add(executor.submit(scan_cached_row, row, service.database, start_date, end_date))
                # 只有补数据路径需要限速，避免免费行情源被短时间打爆。
                time.sleep(0.2)
            yield from drain_completed(pending)
            while len(pending) >= max_pending:
                wait_if_paused(control, service.database, run_id)
                yield wait_for_next_result(pending)
        while pending:
            wait_if_paused(control, service.database, run_id)
            yield wait_for_next_result(pending)


def can_compute_from_database(row, service, start_date, end_date):
    return service.daily_cache_ready(row["ts_code"], start_date, end_date)


def prepare_daily_cache(row, service, start_date, end_date):
    ts_code = row["ts_code"]
    try:
        service.get_daily_data(ts_code, start_date, end_date)
        if service.daily_cache_ready(ts_code, start_date, end_date):
            return None
        return RuntimeError("日线缓存仍不满足策略计算窗口")
    except Exception as exc:
        return exc


def drain_completed(pending):
    done = [future for future in pending if future.done()]
    for future in done:
        pending.remove(future)
        yield future.result()


def wait_for_next_result(pending):
    future = next(as_completed(pending))
    pending.remove(future)
    return future.result()


def scan_cached_row(row: dict, database, start_date, end_date) -> dict:
    ts_code = row["ts_code"]
    data = database.load_klines(ts_code, "D", start_date, end_date)
    prepared_data = MarketDataService._prepare_result(data)
    return evaluate_stock_row(row, prepared_data)


def evaluate_stock_row(row: dict, daily_data: pd.DataFrame) -> dict:
    ts_code = row["ts_code"]
    name = row["name"]
    asset_type = row.get("asset_type", "stock")
    logger.debug("开始计算策略: %s %s", ts_code, name)
    try:
        results = evaluate_daily_strategies(daily_data, ts_code, name)
        details = build_daily_signal_details(daily_data, results)
        return {
            "ts_code": ts_code,
            "name": name,
            "asset_type": asset_type,
            "results": results,
            "details": details,
            "error": None,
        }
    except Exception as exc:
        logger.exception("策略计算失败: %s %s", ts_code, name)
        return {"ts_code": ts_code, "name": name, "asset_type": asset_type, "results": [], "details": {}, "error": exc}


def wait_if_paused(control, database, run_id):
    if control:
        control.wait_if_paused(
            on_pause=lambda: database.update_scan_run_status(run_id, "paused"),
            on_resume=lambda: database.update_scan_run_status(run_id, "running"),
        )


def handle_scan_result(context, result, processed_stocks, matched_stocks, error_count):
    ts_code = result["ts_code"]
    name = result["name"]
    asset_type = result.get("asset_type", "stock")
    if result["error"]:
        exc = result["error"]
        context.service.database.save_scan_error(
            context.run_id, ts_code, name, type(exc).__name__, str(exc) or repr(exc), asset_type=asset_type
        )
        error_count += 1
        processed_stocks += 1
        update_scan_progress(context.service.database, context.run_id, processed_stocks, matched_stocks, error_count)
        return processed_stocks, matched_stocks, error_count
    matched_signal_types = []
    for status in result["results"]:
        if status == StockStatus.NO_MATCH:
            continue
        matched_signal_types.append(status.value)
        logger.info("策略命中: %s %s - %s", ts_code, name, status.value)
        context.files[status].write(f"{ts_code} {name}\n")
        context.files[status].flush()
    if matched_signal_types:
        matched_stocks += 1
        context.service.database.save_stock_signals(
            context.run_id,
            ts_code,
            name,
            matched_signal_types,
            asset_type=asset_type,
            signal_details=result.get("details"),
        )
    processed_stocks += 1
    if processed_stocks % 25 == 0:
        update_scan_progress(context.service.database, context.run_id, processed_stocks, matched_stocks, error_count)
    return processed_stocks, matched_stocks, error_count


def update_scan_progress(database, run_id, processed_stocks, matched_stocks, error_count):
    database.update_scan_run(run_id, processed_stocks, matched_stocks, error_count)


def retry_scan_errors(run_id, service=None, control=None):
    """重试原扫描任务中的失败标的，并将本次重试作为独立任务持久化。"""
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
        raise ValueError(f"扫描任务没有可重试的失败标的: {run_id}")
    task_run_id = database.start_retry_run(run_id, len(errors))
    processed_count = 0
    resolved_count = 0
    failed_count = 0
    try:
        if control:
            control.set_run_id(task_run_id)
        for item in errors:
            if control:
                control.wait_if_paused(
                    on_pause=lambda: database.update_scan_run_status(task_run_id, "paused"),
                    on_resume=lambda: database.update_scan_run_status(task_run_id, "running"),
                )
            ts_code = item["ts_code"]
            stock_name = item["stock_name"]
            asset_type = item.get("asset_type", "stock")
            try:
                start_date, end_date = daily_strategy_window()
                daily_data = market_data_service.get_daily_data(ts_code, start_date, end_date)
                results = evaluate_daily_strategies(daily_data, ts_code, stock_name)
                signal_types = [status.value for status in results if status != StockStatus.NO_MATCH]
                signal_details = build_daily_signal_details(daily_data, results)
                database.save_stock_signals(
                    run_id,
                    ts_code,
                    stock_name,
                    signal_types,
                    asset_type=asset_type,
                    signal_details=signal_details,
                )
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
                    asset_type=asset_type,
                )
                failed_count += 1
            processed_count += 1
            # 重试标的数量通常较少，逐条落库才能让任务队列及时展示真实进度。
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
        logger.debug("已删除空策略结果文件: %s", file_path)


if __name__ == "__main__":
    scan_market()
