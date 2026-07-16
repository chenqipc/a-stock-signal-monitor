"""Flask Web入口和JSON接口。"""

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template, request

from common.StockEnum import StockStatus
from etf_monitor.watchlist import DEFAULT_WATCHLIST
from market_data.config import (
    DATABASE_FILENAME,
    get_database_configuration,
    get_database_journal_mode,
    get_database_path,
    save_database_configuration,
)
from market_data.database import MarketDataDatabase
from market_data.service import MarketDataService
from market_data.trading_calendar import TRADING_SESSIONS

from .tasks import TaskManager


INDICATOR_TONES = ("emerald", "cyan", "amber", "violet", "rose", "blue")
INDEX_REFRESH_COOLDOWN_SECONDS = 300
INDEX_INTRADAY_CACHE_SECONDS = 120
INDEX_WATCHLIST = (
    {"symbol": "000001.SH", "name": "上证指数", "short_name": "上证"},
    {"symbol": "399001.SZ", "name": "深证成指", "short_name": "深成指"},
    {"symbol": "399006.SZ", "name": "创业板指", "short_name": "创业板"},
    {"symbol": "000300.SH", "name": "沪深300指数", "short_name": "沪深300"},
    {"symbol": "000688.SH", "name": "科创50指数", "short_name": "科创50"},
    {"symbol": "HSI.HK", "name": "恒生指数", "short_name": "恒生"},
    {"symbol": "IXIC.US", "name": "纳斯达克综合指数", "short_name": "纳斯达克", "allow_current": False},
    {"symbol": "GOLD.SGE", "name": "Au99.99现货黄金", "short_name": "黄金现货", "allow_current": False},
)


def create_app(config=None, database=None, task_manager=None):
    """创建可测试的Web应用实例。"""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.json.ensure_ascii = False
    app.config.update(config or {})
    settings_path = app.config.get("DATABASE_SETTINGS_PATH")
    market_database = database or MarketDataDatabase(
        get_database_path(settings_path), get_database_journal_mode(settings_path)
    )
    # 后台任务依附于Web进程，启动时先收尾上次进程遗留的运行中记录。
    if app.config.get("RECOVER_INTERRUPTED_TASKS", True):
        market_database.fail_interrupted_scan_runs()
    app.extensions["market_database"] = market_database
    app.extensions["task_manager"] = task_manager or TaskManager()
    app.extensions["database_settings_path"] = settings_path
    app.extensions["index_refresh_failures"] = {}
    app.extensions["index_refresh_lock"] = threading.Lock()

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "A-Stock Signal Monitor"})

    @app.get("/api/dashboard")
    def dashboard():
        db = _database(app)
        latest_run = db.get_latest_scan_run()
        indicators = _indicator_catalog(db.get_signal_summary())
        return jsonify(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "market": _market_state(db),
                "stats": db.get_dashboard_stats(),
                "latest_scan": latest_run,
                "indicators": indicators,
                "latest_signals": db.get_latest_signals(limit=12),
                "watchlist": _watchlist_payload(db),
                "indices": {"items": _index_watchlist_payload(app, 120, refresh_missing=False), "period": "D"},
                "sources": db.get_source_health(),
                "tasks": _task_manager(app).get_status(),
            }
        )

    @app.get("/api/indicators")
    def indicators():
        asset_type = request.args.get("asset_type", "").strip().lower()
        if asset_type and asset_type not in {"stock", "etf"}:
            return jsonify({"error": "资产类型必须是 stock 或 etf"}), 400
        return jsonify({"items": _indicator_catalog(_database(app).get_signal_summary(asset_type=asset_type))})

    @app.get("/api/indices")
    def indices():
        limit = min(240, _positive_int(request.args.get("limit"), 120))
        refresh_missing = request.args.get("refresh") == "1"
        return jsonify({"items": _index_watchlist_payload(app, limit, refresh_missing=refresh_missing), "period": "D"})

    @app.get("/api/indices/<symbol>/history")
    def index_history(symbol):
        """按当前最早K线向前加载单个指数历史，避免缩放时重复刷新全部指数。"""
        normalized_symbol = MarketDataService.normalize_symbol(symbol)
        if normalized_symbol not in {item["symbol"] for item in INDEX_WATCHLIST}:
            return jsonify({"error": "该标的不属于首页主要指数"}), 404
        try:
            before = pd.Timestamp(request.args.get("before") or datetime.now().date())
            if pd.isna(before):
                raise ValueError("日期为空")
        except (TypeError, ValueError):
            return jsonify({"error": "before必须是有效日期"}), 400
        limit = min(240, _positive_int(request.args.get("limit"), 120))
        ensure_data = request.args.get("ensure", "1") != "0"
        with app.extensions["index_refresh_lock"]:
            payload = _index_history_payload(app, normalized_symbol, before, limit, ensure_data)
        return jsonify(payload)

    @app.get("/api/stocks")
    def stocks():
        page = _positive_int(request.args.get("page"), 1)
        page_size = _positive_int(request.args.get("page_size"), 20)
        database = _database(app)
        group_id = _optional_positive_int(request.args.get("group_id"))
        try:
            result = database.search_stocks(
                query=request.args.get("q", "").strip(),
                market=request.args.get("market", "").strip(),
                page=page,
                page_size=page_size,
                group_id=group_id,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        result["groups"] = database.list_instrument_groups("stock")
        result["selected_group_id"] = group_id
        return jsonify(result)

    @app.get("/api/etfs")
    def etfs():
        page = _positive_int(request.args.get("page"), 1)
        page_size = _positive_int(request.args.get("page_size"), 20)
        database = _database(app)
        group_id = _optional_positive_int(request.args.get("group_id"))
        try:
            result = database.search_etfs(
                query=request.args.get("q", "").strip(),
                market=request.args.get("market", "").strip(),
                page=page,
                page_size=page_size,
                group_id=group_id,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        result["groups"] = database.list_instrument_groups("etf")
        result["selected_group_id"] = group_id
        return jsonify(result)

    @app.post("/api/instrument-groups")
    def create_instrument_group():
        body = request.get_json(silent=True) or {}
        try:
            group = _database(app).create_instrument_group(body.get("asset_type"), body.get("name"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"group": group}), 201

    @app.delete("/api/instrument-groups/<int:group_id>")
    def delete_instrument_group(group_id):
        if not _database(app).delete_instrument_group(group_id):
            return jsonify({"error": "分组不存在"}), 404
        return jsonify({"deleted": True})

    @app.post("/api/instrument-groups/<int:group_id>/items")
    def add_instrument_group_item(group_id):
        body = request.get_json(silent=True) or {}
        try:
            added = _database(app).add_instrument_to_group(group_id, body.get("asset_code"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"added": added}), 201 if added else 200

    @app.delete("/api/instrument-groups/<int:group_id>/items/<path:asset_code>")
    def remove_instrument_group_item(group_id, asset_code):
        if not _database(app).remove_instrument_from_group(group_id, asset_code):
            return jsonify({"error": "分组条目不存在"}), 404
        return jsonify({"removed": True})

    @app.patch("/api/instrument-groups/<int:group_id>/items/<path:asset_code>/pin")
    def pin_instrument_group_item(group_id, asset_code):
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get("pinned"), bool):
            return jsonify({"error": "pinned 必须是布尔值"}), 400
        if not _database(app).set_group_item_pinned(group_id, asset_code, body["pinned"]):
            return jsonify({"error": "分组条目不存在"}), 404
        return jsonify({"pinned": body["pinned"]})

    @app.get("/api/signals")
    def signals():
        database = _database(app)
        asset_type = request.args.get("asset_type", "").strip().lower()
        if asset_type and asset_type not in {"stock", "etf"}:
            return jsonify({"error": "资产类型必须是 stock 或 etf"}), 400
        items = database.get_latest_signals(
            limit=_positive_int(request.args.get("limit"), 100),
            signal_type=request.args.get("type", "").strip(),
            query=request.args.get("q", "").strip(),
            asset_type=asset_type,
        )
        indicators = _indicator_catalog(database.get_signal_summary(asset_type=asset_type))
        return jsonify({"items": items, "total": len(items), "asset_type": asset_type, "indicators": indicators})

    @app.get("/api/task-runs")
    @app.get("/api/scan-runs")
    def scan_runs():
        limit = _positive_int(request.args.get("limit"), 10)
        return jsonify({"items": _database(app).get_task_history(limit)})

    @app.delete("/api/task-runs/<int:run_id>")
    def delete_task_run(run_id):
        result = _database(app).delete_task_run(run_id)
        if result == "not_found":
            return jsonify({"error": "任务记录不存在"}), 404
        if result == "running":
            return jsonify({"error": "运行中的任务不能删除，请等待任务结束"}), 409
        return jsonify({"deleted": True, "run_id": run_id})

    @app.get("/api/scan-runs/<int:run_id>/errors")
    def scan_run_errors(run_id):
        database = _database(app)
        scan_run = database.get_scan_run(run_id)
        if not scan_run:
            return jsonify({"error": "扫描任务不存在"}), 404
        summary = database.get_scan_error_summary(run_id)
        summary["untracked"] = max(0, scan_run["error_count"] - summary["unresolved"])
        return jsonify(
            {
                "run": scan_run,
                "summary": summary,
                "items": database.get_scan_errors(run_id, limit=_positive_int(request.args.get("limit"), 500)),
                "can_retry": summary["unresolved"] > 0,
            }
        )

    @app.post("/api/scan-runs/<int:run_id>/retry-errors")
    def retry_scan_run_errors(run_id):
        database = _database(app)
        if not database.get_scan_run(run_id):
            return jsonify({"error": "扫描任务不存在"}), 404
        if database.get_scan_error_summary(run_id)["unresolved"] == 0:
            return jsonify({"error": "该批次没有可重试的失败标的"}), 400
        started, state = _task_manager(app).start_retry_errors(run_id)
        if not started:
            return jsonify({"error": "全市场扫描或错误重试任务正在运行", "task": state}), 409
        return jsonify({"started": True, "task": state}), 202

    @app.get("/api/klines/<symbol>")
    def klines(symbol):
        period = request.args.get("period", "D")
        if period not in {"D", "5min", "15min", "30min", "60min", "120min"}:
            return jsonify({"error": "不支持的K线周期"}), 400
        normalized_symbol = MarketDataService.normalize_symbol(symbol)
        limit = min(500, _positive_int(request.args.get("limit"), 120))
        months = min(24, _positive_int(request.args.get("months"), 3 if period == "D" else 1))
        ensure_data = request.args.get("ensure") == "1" and period == "D"
        end_date = datetime.now().date()
        start_date = (pd.Timestamp(end_date) - pd.DateOffset(months=months)).date()
        database = _database(app)
        data = database.load_klines(normalized_symbol, period, start_date, end_date) if period == "D" else database.load_klines(normalized_symbol, period)
        source = "sqlite_cache" if not data.empty else "unavailable"
        warning = None
        if ensure_data:
            service = MarketDataService(database=database)
            try:
                # 统一服务会先校验SQLite覆盖范围；仅缺少最新日线或成交量时访问网络并写回SQLite。
                missing_chart_fields = not data.empty and not MarketDataService._daily_data_has_chart_fields(data)
                data = service.get_daily_data(normalized_symbol, start_date, end_date, force_refresh=missing_chart_fields)
                source = data.attrs.get("source", source)
                provider_errors = data.attrs.get("provider_errors") or []
                warning = "; ".join(provider_errors) if provider_errors else None
            except Exception as exc:
                warning = str(exc)
                data = database.load_klines(normalized_symbol, period, start_date, end_date)
                source = "sqlite_stale_cache" if not data.empty else "unavailable"
            finally:
                service.close()
        data = data.tail(limit)
        return jsonify(
            {
                "symbol": normalized_symbol,
                "period": period,
                "source": source,
                "warning": warning,
                "has_volume": MarketDataService._daily_data_has_volume(data) if period == "D" else False,
                "has_ohlcv": MarketDataService._daily_data_has_chart_fields(data) if period == "D" else False,
                "items": _frame_records(data),
            }
        )

    @app.get("/api/tasks")
    def tasks():
        return jsonify(_task_manager(app).get_status())

    @app.get("/api/settings/database")
    def database_settings():
        return jsonify(_database_settings_payload(app))

    @app.post("/api/settings/database")
    def update_database_settings():
        """安全切换数据库目录；目标不存在时可复制当前数据库快照。"""
        body = request.get_json(silent=True) or {}
        configuration = get_database_configuration(app.extensions["database_settings_path"])
        if configuration["managed_by_environment"]:
            return jsonify({"error": "数据库路径由 MARKET_DATA_DB 环境变量管理，无法在网页中修改"}), 409
        running_tasks = [
            name for name, state in _task_manager(app).get_status().items() if state.get("status") in {"running", "paused"}
        ]
        if running_tasks:
            return jsonify({"error": f"请等待运行中的任务结束后再切换数据库: {', '.join(running_tasks)}"}), 409
        directory = Path(str(body.get("database_directory") or "").strip()).expanduser()
        if not directory.is_absolute():
            return jsonify({"error": "数据库目录必须是绝对路径"}), 400
        directory = directory.resolve()
        copy_current = body.get("copy_current", True)
        cloud_sync_mode = body.get("cloud_sync_mode", False)
        if not isinstance(copy_current, bool) or not isinstance(cloud_sync_mode, bool):
            return jsonify({"error": "copy_current 和 cloud_sync_mode 必须是布尔值"}), 400
        target_path = directory / DATABASE_FILENAME
        current_database = _database(app)
        same_database = target_path == current_database.database_path.resolve()
        target_existed = target_path.exists()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            copied = False
            if not same_database and not target_existed and copy_current:
                current_database.backup_to(target_path)
                copied = True
            journal_mode = "DELETE" if cloud_sync_mode else "WAL"
            new_database = MarketDataDatabase(target_path, journal_mode)
            save_database_configuration(
                directory, cloud_sync_mode, settings_path=app.extensions["database_settings_path"]
            )
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            return jsonify({"error": f"数据库目录切换失败: {exc}"}), 400
        app.extensions["market_database"] = new_database
        payload = _database_settings_payload(app)
        payload.update({"copied_current_database": copied, "used_existing_database": target_existed})
        return jsonify(payload)

    @app.get("/api/task-progress")
    def task_progress():
        """一次返回实时状态、最新扫描和持久化任务队列，减少前端轮询请求。"""
        limit = _positive_int(request.args.get("limit"), 20)
        database = _database(app)
        task_runs = database.get_task_history(limit)
        return jsonify(
            {
                "tasks": _task_manager(app).get_status(),
                "latest_scan": database.get_latest_scan_run(),
                "task_runs": task_runs,
                "scan_runs": task_runs,
            }
        )

    @app.post("/api/tasks/refresh-stocks")
    def refresh_stocks_task():
        started, state = _task_manager(app).start_refresh_stocks()
        return jsonify({"started": started, "task": state}), 202 if started else 409

    @app.post("/api/tasks/refresh-etfs")
    def refresh_etfs_task():
        started, state = _task_manager(app).start_refresh_etfs()
        return jsonify({"started": started, "task": state}), 202 if started else 409

    @app.post("/api/tasks/scan-market")
    def scan_market_task():
        payload = request.get_json(silent=True) or {}
        scan_scope = str(payload.get("scan_scope", "all")).strip().lower()
        if scan_scope not in {"all", "stock", "etf"}:
            return jsonify({"error": "扫描范围仅支持 all、stock 或 etf"}), 400
        started, state = _task_manager(app).start_scan_market(scan_scope)
        if not started:
            return jsonify({"error": "全市场扫描或错误重试任务正在运行", "started": False, "task": state}), 409
        return jsonify({"started": True, "task": state}), 202

    @app.post("/api/tasks/<task_name>/pause")
    def pause_task(task_name):
        changed, state = _task_manager(app).pause_task(task_name)
        if not changed:
            return jsonify({"error": "任务当前不可暂停", "task": state}), 409
        if state.get("run_id"):
            _database(app).update_scan_run_status(state["run_id"], "paused")
        return jsonify({"task": state})

    @app.post("/api/tasks/<task_name>/resume")
    def resume_task(task_name):
        changed, state = _task_manager(app).resume_task(task_name)
        if not changed:
            return jsonify({"error": "任务当前不可继续", "task": state}), 409
        if state.get("run_id"):
            _database(app).update_scan_run_status(state["run_id"], "running")
        return jsonify({"task": state})

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "接口不存在"}), 404
        return render_template("index.html"), 404

    return app


def _database(app):
    return app.extensions["market_database"]


def _task_manager(app):
    return app.extensions["task_manager"]


def _database_settings_payload(app):
    database = _database(app)
    configuration = get_database_configuration(app.extensions["database_settings_path"])
    database_path = database.database_path.resolve()
    return {
        "database_directory": str(database_path.parent),
        "database_path": str(database_path),
        "database_filename": DATABASE_FILENAME,
        "exists": database_path.exists(),
        "size_bytes": database_path.stat().st_size if database_path.exists() else 0,
        "cloud_sync_mode": database.journal_mode == "DELETE",
        "journal_mode": database.journal_mode,
        "managed_by_environment": configuration["managed_by_environment"],
    }


def _indicator_catalog(summary):
    count_by_label = {item["signal_type"]: item["count"] for item in summary}
    indicators = []
    for index, status in enumerate(StockStatus):
        if status == StockStatus.NO_MATCH:
            continue
        indicators.append(
            {
                "key": status.name,
                "label": status.value,
                "count": count_by_label.get(status.value, 0),
                "tone": INDICATOR_TONES[index % len(INDICATOR_TONES)],
            }
        )
    return indicators


def _market_state(database):
    now = datetime.now()
    trading_day = database.get_trading_day(now.date())
    if trading_day is None:
        trading_day = now.weekday() < 5
    current_time = now.time().replace(tzinfo=None)
    is_open = trading_day and any(start <= current_time <= end for start, end in TRADING_SESSIONS)
    if is_open:
        label = "交易中"
    elif trading_day and current_time < TRADING_SESSIONS[0][0]:
        label = "等待开盘"
    else:
        label = "已休市"
    return {"is_open": is_open, "is_trading_day": trading_day, "label": label, "time": now.strftime("%H:%M:%S")}


def _watchlist_payload(database):
    symbols = [item["symbol"] for item in DEFAULT_WATCHLIST]
    minute_bars = {item["symbol"]: item for item in database.get_latest_bars(symbols, "15min")}
    daily_bars = {item["symbol"]: item for item in database.get_latest_bars(symbols, "D")}
    result = []
    for watch_item in DEFAULT_WATCHLIST:
        bar = minute_bars.get(watch_item["symbol"]) or daily_bars.get(watch_item["symbol"])
        result.append(
            {
                **watch_item,
                "close": bar.get("close") if bar else None,
                "pct_chg": bar.get("pct_chg") if bar else None,
                "period": bar.get("period") if bar else None,
                "trade_time": bar.get("trade_time") if bar else None,
                "source": bar.get("source") if bar else None,
            }
        )
    return result


def _index_watchlist_payload(app, limit, refresh_missing=False):
    if refresh_missing:
        # 串行合并并发刷新；后进入的请求会直接复用前一个请求刚写入SQLite的数据。
        with app.extensions["index_refresh_lock"]:
            return _load_index_watchlist_payload(app, limit, refresh_missing=True)
    return _load_index_watchlist_payload(app, limit, refresh_missing=False)


def _load_index_watchlist_payload(app, limit, refresh_missing=False):
    database = _database(app)
    service = MarketDataService(database=database)
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=max(180, limit * 2))
    result = []
    try:
        for item in INDEX_WATCHLIST:
            result.append(_index_payload_item(app, database, service, item, start_date, end_date, limit, refresh_missing))
    finally:
        service.close()
    return result


def _index_payload_item(app, database, service, item, start_date, end_date, limit, refresh_missing=False):
    source = "sqlite_cache"
    error = None
    data = database.load_klines(item["symbol"], "D", start_date, end_date)
    allow_current = item.get("allow_current", True)
    needs_refresh = _index_data_needs_refresh(database, item["symbol"], data, end_date, allow_current)
    if needs_refresh and _index_refresh_is_cooling(app, item["symbol"]):
        needs_refresh = False
    try:
        if refresh_missing and needs_refresh and not _index_refresh_is_cooling(app, item["symbol"]):
            target_date = _latest_cached_index_target_date(database, end_date, allow_current)
            data = service.get_daily_data(
                item["symbol"], start_date, end_date, minimum_trade_time=target_date, refresh_latest=True
            )
            source = data.attrs.get("source")
            still_stale = _index_data_needs_refresh(database, item["symbol"], data, end_date, allow_current)
            if still_stale:
                _remember_index_refresh_failure(app, item["symbol"])
                provider_errors = data.attrs.get("provider_errors") or []
                error = "; ".join(provider_errors) or "行情源尚未返回当日指数数据"
            else:
                _clear_index_refresh_failure(app, item["symbol"])
            needs_refresh = still_stale and not _index_refresh_is_cooling(app, item["symbol"])
    except Exception as exc:
        # 指数补数失败不应该拖慢每次首页打开；失败后短时间内只展示缓存并等待下次冷却结束重试。
        _remember_index_refresh_failure(app, item["symbol"])
        error = str(exc)
        data = database.load_klines(item["symbol"], "D", start_date, end_date)
        source = "sqlite_stale_cache" if not data.empty else "unavailable"
        needs_refresh = data.empty and not _index_refresh_is_cooling(app, item["symbol"])
    if data.empty:
        source = "unavailable"
    data = data.tail(limit)
    points = _frame_records(data)
    latest = points[-1] if points else {}
    close = latest.get("close")
    pct_chg = latest.get("pct_chg")
    range_pct = _range_percent(data)
    return {
        **item,
        "close": close,
        "pct_chg": pct_chg,
        "range_pct": range_pct,
        "trade_time": latest.get("trade_time"),
        "source": source,
        "error": error,
        "needs_refresh": needs_refresh,
        "points": points,
    }


def _index_history_payload(app, symbol, before, limit, ensure_data=True):
    """返回before之前的一批K线；缓存不足时只补该指数的更早区间。"""
    database = _database(app)
    end_date = before.normalize() - pd.Timedelta(1, unit="D")
    start_date = end_date - pd.Timedelta(max(365, limit * 3), unit="D")
    data = database.load_klines(symbol, "D", start_date, end_date)
    source = "sqlite_cache" if not data.empty else "unavailable"
    warning = None
    if ensure_data and len(data) < limit:
        service = MarketDataService(database=database)
        try:
            data = service.get_daily_data(symbol, start_date, end_date)
            source = data.attrs.get("source", source)
            provider_errors = data.attrs.get("provider_errors") or []
            warning = "; ".join(provider_errors) if provider_errors else None
        except Exception as exc:
            warning = str(exc)
            data = database.load_klines(symbol, "D", start_date, end_date)
            source = "sqlite_stale_cache" if not data.empty else "unavailable"
        finally:
            service.close()
    data = data[data["trade_time"] < before].tail(limit) if not data.empty else data
    points = _frame_records(data)
    return {
        "symbol": symbol,
        "period": "D",
        "source": source,
        "warning": warning,
        "has_more": len(points) >= limit,
        "items": points,
    }


def _index_refresh_is_cooling(app, symbol):
    failure_time = app.extensions["index_refresh_failures"].get(symbol)
    if not failure_time:
        return False
    return (datetime.now() - failure_time).total_seconds() < INDEX_REFRESH_COOLDOWN_SECONDS


def _remember_index_refresh_failure(app, symbol):
    app.extensions["index_refresh_failures"][symbol] = datetime.now()


def _clear_index_refresh_failure(app, symbol):
    app.extensions["index_refresh_failures"].pop(symbol, None)


def _index_data_needs_refresh(database, symbol, data, end_date, allow_current=True):
    if data is None or data.empty:
        return True
    expected_date = _latest_cached_index_target_date(database, end_date, allow_current)
    latest = pd.to_datetime(data["trade_time"]).max().normalize()
    state = database.get_fetch_state(symbol, "D") or {}
    if allow_current and _index_target_is_intraday(expected_date):
        return latest < expected_date or not _index_cache_is_recent(state)
    checked_end = pd.Timestamp(state["coverage_end"]).normalize() if state.get("coverage_end") else None
    return latest < expected_date and (checked_end is None or checked_end < expected_date)


def _latest_cached_index_target_date(database, end_date, allow_current=True):
    candidate = min(pd.Timestamp(end_date).date(), datetime.now().date())
    market_open_time = TRADING_SESSIONS[0][0]
    if candidate == datetime.now().date() and (not allow_current or datetime.now().time() < market_open_time):
        candidate -= timedelta(days=1)
    for _ in range(15):
        trading_day = database.get_trading_day(candidate)
        if trading_day is None:
            trading_day = candidate.weekday() < 5
        if trading_day:
            return pd.Timestamp(candidate)
        candidate -= timedelta(days=1)
    return pd.Timestamp(candidate)


def _index_target_is_intraday(expected_date):
    now = datetime.now()
    ready_time = datetime.strptime("15:30", "%H:%M").time()
    return pd.Timestamp(expected_date).date() == now.date() and TRADING_SESSIONS[0][0] <= now.time() < ready_time


def _index_cache_is_recent(state):
    last_success_at = state.get("last_success_at")
    if not last_success_at:
        return False
    updated_at = pd.Timestamp(last_success_at)
    if updated_at.tzinfo is None:
        updated_at = updated_at.tz_localize("UTC")
    return pd.Timestamp.now(tz="UTC") - updated_at <= pd.Timedelta(seconds=INDEX_INTRADAY_CACHE_SECONDS)


def _range_percent(data):
    if data is None or data.empty or len(data) < 2:
        return None
    first = pd.to_numeric(data["close"], errors="coerce").dropna()
    if len(first) < 2 or first.iloc[0] == 0:
        return None
    return (first.iloc[-1] / first.iloc[0] - 1) * 100


def _positive_int(value, default):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _frame_records(data):
    if data is None or data.empty:
        return []
    normalized = data.copy()
    for column in normalized.columns:
        if pd.api.types.is_datetime64_any_dtype(normalized[column]):
            normalized[column] = normalized[column].dt.strftime("%Y-%m-%d %H:%M:%S")
    normalized = normalized.astype(object).where(pd.notna(normalized), None)
    return normalized.to_dict(orient="records")


def _optional_positive_int(value):
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
