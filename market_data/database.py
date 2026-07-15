"""基于 SQLite 的历史K线、交易日历和通知状态存储。"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from market_data.error_classifier import classify_scan_error


KLINE_COLUMNS = [
    "trade_time", "open", "close", "high", "low", "vol", "amount", "pre_close", "pct_chg", "turnover_rate", "is_st"
]
ASSET_TABLES = {"stock": "stock_master", "etf": "etf_master"}
TASK_TYPE_MARKET_SCAN = "market_scan"
TASK_TYPE_ERROR_RETRY = "error_retry"


class MarketDataDatabase:
    """提供小规模股票监控所需的 SQLite 持久化能力。"""

    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS kline (
                    symbol TEXT NOT NULL,
                    period TEXT NOT NULL,
                    trade_time TEXT NOT NULL,
                    open REAL,
                    close REAL,
                    high REAL,
                    low REAL,
                    vol REAL,
                    amount REAL,
                    pre_close REAL,
                    pct_chg REAL,
                    turnover_rate REAL,
                    is_st INTEGER,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, period, trade_time)
                );
                CREATE INDEX IF NOT EXISTS idx_kline_query ON kline(symbol, period, trade_time);
                CREATE TABLE IF NOT EXISTS fetch_state (
                    symbol TEXT NOT NULL,
                    period TEXT NOT NULL,
                    source TEXT NOT NULL,
                    last_success_at TEXT NOT NULL,
                    coverage_start TEXT,
                    coverage_end TEXT,
                    PRIMARY KEY (symbol, period)
                );
                CREATE TABLE IF NOT EXISTS trade_calendar (
                    calendar_date TEXT PRIMARY KEY,
                    is_trading_day INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notification_state (
                    notification_key TEXT PRIMARY KEY,
                    trade_time TEXT NOT NULL,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stock_master (
                    ts_code TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    market TEXT,
                    list_date TEXT,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_stock_master_name ON stock_master(name);
                CREATE TABLE IF NOT EXISTS etf_master (
                    ts_code TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    market TEXT,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_etf_master_name ON etf_master(name);
                CREATE TABLE IF NOT EXISTS instrument_group (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_type TEXT NOT NULL CHECK(asset_type IN ('stock', 'etf')),
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(asset_type, name)
                );
                CREATE TABLE IF NOT EXISTS instrument_group_item (
                    group_id INTEGER NOT NULL,
                    asset_code TEXT NOT NULL,
                    is_pinned INTEGER NOT NULL DEFAULT 0,
                    pinned_at TEXT,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY(group_id, asset_code),
                    FOREIGN KEY(group_id) REFERENCES instrument_group(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_group_item_order ON instrument_group_item(group_id, is_pinned, pinned_at, added_at);
                CREATE TABLE IF NOT EXISTS scan_run (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT NOT NULL DEFAULT 'market_scan',
                    parent_run_id INTEGER,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    total_stocks INTEGER NOT NULL DEFAULT 0,
                    processed_stocks INTEGER NOT NULL DEFAULT 0,
                    matched_stocks INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT
                );
                CREATE TABLE IF NOT EXISTS stock_signal (
                    run_id INTEGER NOT NULL,
                    ts_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, ts_code, signal_type),
                    FOREIGN KEY (run_id) REFERENCES scan_run(id)
                );
                CREATE INDEX IF NOT EXISTS idx_stock_signal_run_type ON stock_signal(run_id, signal_type);
                CREATE TABLE IF NOT EXISTS scan_error (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    ts_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'failed',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    first_error_type TEXT NOT NULL,
                    first_error_message TEXT NOT NULL,
                    last_error_type TEXT NOT NULL,
                    last_error_message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (run_id, ts_code),
                    FOREIGN KEY (run_id) REFERENCES scan_run(id)
                );
                CREATE INDEX IF NOT EXISTS idx_scan_error_run_status ON scan_error(run_id, status);
                """
            )
            # 为已有数据库执行轻量迁移，不要求用户删除历史缓存。
            self._ensure_column(connection, "fetch_state", "coverage_start", "TEXT")
            self._ensure_column(connection, "fetch_state", "coverage_end", "TEXT")
            self._ensure_column(connection, "scan_run", "task_type", "TEXT NOT NULL DEFAULT 'market_scan'")
            self._ensure_column(connection, "scan_run", "parent_run_id", "INTEGER")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_scan_run_task_type ON scan_run(task_type, id DESC)")

    @staticmethod
    def _ensure_column(connection, table, column, definition):
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _utc_now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def save_klines(self, symbol, period, data, source, coverage_start=None, coverage_end=None):
        if data is None or data.empty:
            return
        rows = []
        updated_at = self._utc_now()
        coverage_start = self._coverage_value(coverage_start, data["trade_time"].min())
        coverage_end = self._coverage_value(coverage_end, data["trade_time"].max())
        for _, row in data.iterrows():
            values = [self._value_or_none(row.get(column)) for column in KLINE_COLUMNS]
            rows.append((symbol, period, *values, source, updated_at))
        placeholders = ",".join("?" for _ in range(15))
        columns = "symbol,period," + ",".join(KLINE_COLUMNS) + ",source,updated_at"
        updates = ",".join(f"{column}=excluded.{column}" for column in KLINE_COLUMNS[1:] + ["source", "updated_at"])
        sql = f"INSERT INTO kline ({columns}) VALUES ({placeholders}) ON CONFLICT(symbol,period,trade_time) DO UPDATE SET {updates}"
        with self._connect() as connection:
            connection.executemany(sql, rows)
            connection.execute(
                """
                INSERT INTO fetch_state(symbol, period, source, last_success_at, coverage_start, coverage_end)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, period) DO UPDATE SET
                    source = excluded.source,
                    last_success_at = excluded.last_success_at,
                    coverage_start = CASE
                        WHEN fetch_state.coverage_start IS NULL OR excluded.coverage_start < fetch_state.coverage_start
                        THEN excluded.coverage_start ELSE fetch_state.coverage_start END,
                    coverage_end = CASE
                        WHEN fetch_state.coverage_end IS NULL OR excluded.coverage_end > fetch_state.coverage_end
                        THEN excluded.coverage_end ELSE fetch_state.coverage_end END
                """,
                (symbol, period, source, updated_at, coverage_start, coverage_end),
            )

    @staticmethod
    def _coverage_value(value, fallback):
        return pd.Timestamp(fallback if value is None else value).isoformat(sep=" ")

    @staticmethod
    def _value_or_none(value):
        if pd.isna(value):
            return None
        if isinstance(value, pd.Timestamp):
            return value.isoformat(sep=" ")
        return value.item() if hasattr(value, "item") else value

    def load_klines(self, symbol, period, start_date=None, end_date=None):
        conditions = ["symbol = ?", "period = ?"]
        params = [symbol, period]
        if start_date:
            conditions.append("trade_time >= ?")
            params.append(pd.Timestamp(start_date).isoformat(sep=" "))
        if end_date:
            conditions.append("trade_time < ?")
            params.append(self._exclusive_end(end_date))
        sql = f"SELECT {','.join(KLINE_COLUMNS)}, source FROM kline WHERE {' AND '.join(conditions)} ORDER BY trade_time"
        with self._connect() as connection:
            data = pd.read_sql_query(sql, connection, params=params)
        if not data.empty:
            data["trade_time"] = pd.to_datetime(data["trade_time"])
        return data

    @staticmethod
    def _exclusive_end(end_date):
        parsed = pd.Timestamp(end_date)
        if parsed.time() == datetime.min.time():
            # 使用非原地计算，兼容新版Pandas对时间分辨率的严格检查。
            parsed = parsed + pd.DateOffset(days=1)
        else:
            parsed = parsed + pd.DateOffset(microseconds=1)
        return parsed.isoformat(sep=" ")

    def get_fetch_state(self, symbol, period):
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT source, last_success_at, coverage_start, coverage_end
                FROM fetch_state WHERE symbol = ? AND period = ?
                """,
                (symbol, period),
            ).fetchone()
        return dict(row) if row else None

    def save_trade_calendar(self, data, source):
        if data is None or data.empty:
            return
        updated_at = self._utc_now()
        rows = [(str(row["calendar_date"]), int(row["is_trading_day"]), source, updated_at) for _, row in data.iterrows()]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO trade_calendar(calendar_date, is_trading_day, source, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(calendar_date) DO UPDATE SET is_trading_day=excluded.is_trading_day,
                source=excluded.source, updated_at=excluded.updated_at
                """,
                rows,
            )
    def get_trading_day(self, calendar_date):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT is_trading_day FROM trade_calendar WHERE calendar_date = ?", (str(calendar_date),)
            ).fetchone()
        return bool(row[0]) if row else None

    def replace_stock_list(self, data, source):
        """在同一事务中替换证券主数据快照，失败时保留原有列表。"""
        required_columns = {"ts_code", "symbol", "name"}
        if data is None or data.empty:
            raise ValueError("证券列表不能为空")
        missing_columns = required_columns - set(data.columns)
        if missing_columns:
            raise ValueError(f"证券列表缺少字段: {sorted(missing_columns)}")

        updated_at = self._utc_now()
        normalized = data.drop_duplicates("ts_code", keep="last")
        rows = []
        for _, row in normalized.iterrows():
            rows.append(
                (
                    str(row["ts_code"]),
                    str(row["symbol"]),
                    str(row["name"]),
                    self._text_or_empty(row.get("market")),
                    self._text_or_empty(row.get("list_date")),
                    source,
                    updated_at,
                )
            )
        with self._connect() as connection:
            connection.execute("DELETE FROM stock_master")
            connection.executemany(
                """
                INSERT INTO stock_master(ts_code, symbol, name, market, list_date, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            # 股票主表刷新后同步清理不再存在的自定义分组成员。
            connection.execute(
                """
                DELETE FROM instrument_group_item
                WHERE group_id IN (SELECT id FROM instrument_group WHERE asset_type = 'stock')
                  AND asset_code NOT IN (SELECT ts_code FROM stock_master)
                """
            )

    @staticmethod
    def _text_or_empty(value):
        return "" if value is None or pd.isna(value) else str(value)

    def load_stock_list(self):
        with self._connect() as connection:
            return pd.read_sql_query(
                """
                SELECT ts_code, symbol, name, market, list_date, source, updated_at
                FROM stock_master
                ORDER BY ts_code
                """,
                connection,
            )

    def search_stocks(self, query="", market="", page=1, page_size=20, group_id=None):
        """分页检索证券主数据，供Web页面复用。"""
        return self._search_instruments("stock", query, market, page, page_size, group_id)

    def replace_etf_list(self, data, source):
        """原子替换ETF主数据快照，网络刷新失败时不会破坏旧列表。"""
        required_columns = {"ts_code", "symbol", "name"}
        if data is None or data.empty:
            raise ValueError("ETF列表不能为空")
        missing_columns = required_columns - set(data.columns)
        if missing_columns:
            raise ValueError(f"ETF列表缺少字段: {sorted(missing_columns)}")
        updated_at = self._utc_now()
        normalized = data.drop_duplicates("ts_code", keep="last")
        rows = [
            (
                str(row["ts_code"]),
                str(row["symbol"]),
                str(row["name"]),
                self._text_or_empty(row.get("market")),
                source,
                updated_at,
            )
            for _, row in normalized.iterrows()
        ]
        with self._connect() as connection:
            connection.execute("DELETE FROM etf_master")
            connection.executemany(
                """
                INSERT INTO etf_master(ts_code, symbol, name, market, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            # 主列表刷新后清理已经失效的分组成员，避免分组计数出现幽灵条目。
            connection.execute(
                """
                DELETE FROM instrument_group_item
                WHERE group_id IN (SELECT id FROM instrument_group WHERE asset_type = 'etf')
                  AND asset_code NOT IN (SELECT ts_code FROM etf_master)
                """
            )

    def load_etf_list(self):
        with self._connect() as connection:
            return pd.read_sql_query(
                "SELECT ts_code, symbol, name, market, source, updated_at FROM etf_master ORDER BY ts_code",
                connection,
            )

    def search_etfs(self, query="", market="", page=1, page_size=20, group_id=None):
        """分页检索ETF主数据，并在自定义分组中优先返回置顶条目。"""
        return self._search_instruments("etf", query, market, page, page_size, group_id)

    def _search_instruments(self, asset_type, query, market, page, page_size, group_id):
        table = self._asset_table(asset_type)
        conditions = []
        params = []
        if query:
            conditions.append("(m.ts_code LIKE ? OR m.symbol LIKE ? OR m.name LIKE ?)")
            keyword = f"%{query}%"
            params.extend([keyword, keyword, keyword])
        if market:
            conditions.append("m.market = ?")
            params.append(market)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        page = max(1, int(page))
        page_size = min(100, max(1, int(page_size)))
        offset = (page - 1) * page_size
        with self._connect() as connection:
            join_clause = ""
            query_params = list(params)
            if group_id is not None:
                self._require_group(connection, int(group_id), asset_type)
                join_clause = "JOIN instrument_group_item gi ON gi.asset_code = m.ts_code AND gi.group_id = ?"
                query_params = [int(group_id), *params]
            list_date = "m.list_date" if asset_type == "stock" else "'' AS list_date"
            pin_column = "gi.is_pinned" if group_id is not None else "0 AS is_pinned"
            order_clause = "gi.is_pinned DESC, gi.pinned_at DESC, gi.added_at DESC, m.ts_code" if group_id else "m.ts_code"
            total = connection.execute(
                f"SELECT COUNT(*) FROM {table} m {join_clause} {where_clause}", query_params
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT m.ts_code, m.symbol, m.name, m.market, {list_date}, m.source, m.updated_at, {pin_column}
                FROM {table} m {join_clause} {where_clause}
                ORDER BY {order_clause} LIMIT ? OFFSET ?
                """,
                [*query_params, page_size, offset],
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def _asset_table(asset_type):
        try:
            return ASSET_TABLES[asset_type]
        except KeyError as exc:
            raise ValueError("资产类型必须是 stock 或 etf") from exc

    def list_instrument_groups(self, asset_type):
        """返回指定资产类型的自定义分组及条目、置顶数量。"""
        self._asset_table(asset_type)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT g.id, g.asset_type, g.name, g.created_at, g.updated_at,
                       COUNT(i.asset_code) AS item_count,
                       COALESCE(SUM(CASE WHEN i.is_pinned = 1 THEN 1 ELSE 0 END), 0) AS pinned_count
                FROM instrument_group g
                LEFT JOIN instrument_group_item i ON i.group_id = g.id
                WHERE g.asset_type = ?
                GROUP BY g.id ORDER BY g.created_at, g.id
                """,
                (asset_type,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_instrument_group(self, asset_type, name):
        self._asset_table(asset_type)
        normalized_name = str(name or "").strip()
        if not normalized_name or len(normalized_name) > 30:
            raise ValueError("分组名称长度必须为1到30个字符")
        now = self._utc_now()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO instrument_group(asset_type, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (asset_type, normalized_name, now, now),
                )
                group_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            raise ValueError("同名分组已经存在") from exc
        return self.get_instrument_group(group_id)

    def get_instrument_group(self, group_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM instrument_group WHERE id = ?", (int(group_id),)).fetchone()
        return dict(row) if row else None

    def delete_instrument_group(self, group_id):
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM instrument_group WHERE id = ?", (int(group_id),))
        return cursor.rowcount > 0

    def add_instrument_to_group(self, group_id, asset_code):
        normalized_code = str(asset_code or "").strip().upper()
        with self._connect() as connection:
            group = self._require_group(connection, int(group_id))
            table = self._asset_table(group["asset_type"])
            exists = connection.execute(f"SELECT 1 FROM {table} WHERE ts_code = ?", (normalized_code,)).fetchone()
            if not exists:
                raise ValueError("证券不存在于对应主列表")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO instrument_group_item(group_id, asset_code, added_at)
                VALUES (?, ?, ?)
                """,
                (int(group_id), normalized_code, self._utc_now()),
            )
        return cursor.rowcount > 0

    def remove_instrument_from_group(self, group_id, asset_code):
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM instrument_group_item WHERE group_id = ? AND asset_code = ?",
                (int(group_id), str(asset_code).strip().upper()),
            )
        return cursor.rowcount > 0

    def set_group_item_pinned(self, group_id, asset_code, pinned):
        pinned_value = 1 if pinned else 0
        pinned_at = self._utc_now() if pinned else None
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE instrument_group_item SET is_pinned = ?, pinned_at = ?
                WHERE group_id = ? AND asset_code = ?
                """,
                (pinned_value, pinned_at, int(group_id), str(asset_code).strip().upper()),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _require_group(connection, group_id, asset_type=None):
        row = connection.execute("SELECT * FROM instrument_group WHERE id = ?", (group_id,)).fetchone()
        if not row or (asset_type and row["asset_type"] != asset_type):
            raise ValueError("分组不存在或资产类型不匹配")
        return row

    def start_scan_run(self, total_stocks):
        return self.start_task_run(TASK_TYPE_MARKET_SCAN, total_stocks)

    def start_retry_run(self, source_run_id, total_stocks):
        """为错误重试创建独立任务，并保留与原扫描任务的关联。"""
        return self.start_task_run(TASK_TYPE_ERROR_RETRY, total_stocks, parent_run_id=source_run_id)

    def start_task_run(self, task_type, total_stocks, parent_run_id=None):
        started_at = self._utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO scan_run(task_type, parent_run_id, started_at, status, total_stocks)
                VALUES (?, ?, ?, 'running', ?)
                """,
                (task_type, parent_run_id, started_at, int(total_stocks)),
            )
            return cursor.lastrowid

    def update_scan_run(self, run_id, processed_stocks, matched_stocks, error_count):
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scan_run SET processed_stocks = ?, matched_stocks = ?, error_count = ?
                WHERE id = ?
                """,
                (processed_stocks, matched_stocks, error_count, run_id),
            )

    def finish_scan_run(self, run_id, status, processed_stocks, matched_stocks, error_count, error_message=None):
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scan_run SET completed_at = ?, status = ?, processed_stocks = ?, matched_stocks = ?,
                error_count = ?, error_message = ? WHERE id = ?
                """,
                (self._utc_now(), status, processed_stocks, matched_stocks, error_count, error_message, run_id),
            )

    def fail_interrupted_scan_runs(self, error_message="服务重启，后台任务已中断"):
        """服务进程结束后后台线程无法恢复，将遗留的运行中任务明确标记为失败。"""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE scan_run SET completed_at = ?, status = 'failed', error_message = ?
                WHERE status = 'running'
                """,
                (self._utc_now(), error_message),
            )
        return cursor.rowcount

    def save_stock_signals(self, run_id, ts_code, stock_name, signal_types):
        created_at = self._utc_now()
        rows = [(run_id, ts_code, stock_name, signal_type, created_at) for signal_type in signal_types]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO stock_signal(run_id, ts_code, stock_name, signal_type, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def save_scan_error(self, run_id, ts_code, stock_name, error_type, error_message, increment_retry=False):
        """记录逐股票扫描错误；重试再次失败时保留首次原因并更新最近原因。"""
        now = self._utc_now()
        retry_increment = 1 if increment_retry else 0
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scan_error(
                    run_id, ts_code, stock_name, status, retry_count, first_error_type, first_error_message,
                    last_error_type, last_error_message, created_at, updated_at
                ) VALUES (?, ?, ?, 'failed', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, ts_code) DO UPDATE SET
                    stock_name = excluded.stock_name,
                    status = 'failed',
                    retry_count = scan_error.retry_count + excluded.retry_count,
                    last_error_type = excluded.last_error_type,
                    last_error_message = excluded.last_error_message,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    ts_code,
                    stock_name,
                    retry_increment,
                    error_type,
                    error_message,
                    error_type,
                    error_message,
                    now,
                    now,
                ),
            )

    def resolve_scan_error(self, run_id, ts_code):
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scan_error SET status = 'resolved', retry_count = retry_count + 1, updated_at = ?
                WHERE run_id = ? AND ts_code = ?
                """,
                (self._utc_now(), run_id, ts_code),
            )

    def get_scan_run(self, run_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM scan_run WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_scan_errors(self, run_id, unresolved_only=False, limit=500):
        condition = "AND status = 'failed'" if unresolved_only else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, run_id, ts_code, stock_name, status, retry_count, first_error_type,
                       first_error_message, last_error_type, last_error_message, created_at, updated_at
                FROM scan_error WHERE run_id = ? {condition}
                ORDER BY CASE status WHEN 'failed' THEN 0 ELSE 1 END, updated_at DESC
                LIMIT ?
                """,
                (run_id, min(1000, max(1, int(limit)))),
            ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["error_category"], item["error_summary"] = classify_scan_error(
                item["last_error_type"], item["last_error_message"]
            )
        return items

    def get_scan_error_summary(self, run_id):
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT last_error_type AS error_type, last_error_message AS error_message
                FROM scan_error WHERE run_id = ? AND status = 'failed'
                """,
                (run_id,),
            ).fetchall()
            counts = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS unresolved,
                       SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) AS resolved
                FROM scan_error WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        grouped_errors = {}
        for row in rows:
            error_type, error_message = classify_scan_error(row["error_type"], row["error_message"])
            key = (error_type, error_message)
            grouped_errors[key] = grouped_errors.get(key, 0) + 1
        groups = [
            {"error_type": key[0], "error_message": key[1], "count": count}
            for key, count in sorted(grouped_errors.items(), key=lambda item: (-item[1], item[0][0]))
        ]
        return {
            "groups": groups,
            "total": counts["total"] or 0,
            "unresolved": counts["unresolved"] or 0,
            "resolved": counts["resolved"] or 0,
        }

    def refresh_scan_run_counts(self, run_id):
        """错误重试后重新汇总命中股票和未解决错误数量。"""
        with self._connect() as connection:
            matched_stocks = connection.execute(
                "SELECT COUNT(DISTINCT ts_code) FROM stock_signal WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            error_count = connection.execute(
                "SELECT COUNT(*) FROM scan_error WHERE run_id = ? AND status = 'failed'", (run_id,)
            ).fetchone()[0]
            connection.execute(
                "UPDATE scan_run SET matched_stocks = ?, error_count = ? WHERE id = ?",
                (matched_stocks, error_count, run_id),
            )
        return {"matched_stocks": matched_stocks, "error_count": error_count}

    def get_latest_scan_run(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scan_run WHERE task_type = ? ORDER BY id DESC LIMIT 1", (TASK_TYPE_MARKET_SCAN,)
            ).fetchone()
        return dict(row) if row else None

    def get_task_history(self, limit=10):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM scan_run ORDER BY id DESC LIMIT ?", (min(50, max(1, int(limit))),)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_scan_history(self, limit=10):
        """兼容旧调用方；页面展示已升级为包含扫描和重试的任务队列。"""
        return self.get_task_history(limit)

    def get_signal_summary(self, run_id=None):
        run_id = run_id or self._latest_completed_run_id()
        if not run_id:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT signal_type, COUNT(*) AS count
                FROM stock_signal WHERE run_id = ? GROUP BY signal_type ORDER BY count DESC, signal_type
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_signals(self, limit=100, signal_type="", query=""):
        run_id = self._latest_completed_run_id()
        if not run_id:
            return []
        conditions = ["run_id = ?"]
        params = [run_id]
        if signal_type:
            conditions.append("signal_type = ?")
            params.append(signal_type)
        if query:
            conditions.append("(ts_code LIKE ? OR stock_name LIKE ?)")
            keyword = f"%{query}%"
            params.extend([keyword, keyword])
        params.append(min(500, max(1, int(limit))))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT run_id, ts_code, stock_name, signal_type, created_at
                FROM stock_signal WHERE {' AND '.join(conditions)}
                ORDER BY signal_type, ts_code LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def _latest_completed_run_id(self):
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM scan_run
                WHERE status = 'completed' AND task_type = ?
                ORDER BY id DESC LIMIT 1
                """,
                (TASK_TYPE_MARKET_SCAN,),
            ).fetchone()
        return row[0] if row else None

    def get_dashboard_stats(self):
        with self._connect() as connection:
            stock_count = connection.execute("SELECT COUNT(*) FROM stock_master").fetchone()[0]
            etf_count = connection.execute("SELECT COUNT(*) FROM etf_master").fetchone()[0]
            kline_count = connection.execute("SELECT COUNT(*) FROM kline").fetchone()[0]
            symbol_count = connection.execute("SELECT COUNT(DISTINCT symbol) FROM kline").fetchone()[0]
            notification_count = connection.execute("SELECT COUNT(*) FROM notification_state").fetchone()[0]
            stock_updated_at = connection.execute("SELECT MAX(updated_at) FROM stock_master").fetchone()[0]
            etf_updated_at = connection.execute("SELECT MAX(updated_at) FROM etf_master").fetchone()[0]
        return {
            "stock_count": stock_count,
            "etf_count": etf_count,
            "kline_count": kline_count,
            "cached_symbol_count": symbol_count,
            "notification_count": notification_count,
            "stock_updated_at": stock_updated_at,
            "etf_updated_at": etf_updated_at,
        }

    def get_source_health(self):
        """汇总各数据源在本地缓存中的最近成功时间和数据集数量。"""
        sources = {}
        with self._connect() as connection:
            fetch_rows = connection.execute(
                """
                SELECT source, COUNT(*) AS dataset_count, MAX(last_success_at) AS last_success_at
                FROM fetch_state GROUP BY source
                """
            ).fetchall()
            stock_rows = connection.execute(
                """
                SELECT source, COUNT(*) AS dataset_count, MAX(updated_at) AS last_success_at
                FROM stock_master GROUP BY source
                """
            ).fetchall()
            etf_rows = connection.execute(
                """
                SELECT source, COUNT(*) AS dataset_count, MAX(updated_at) AS last_success_at
                FROM etf_master GROUP BY source
                """
            ).fetchall()
        for row in [*fetch_rows, *stock_rows, *etf_rows]:
            source = row["source"]
            current = sources.setdefault(source, {"source": source, "dataset_count": 0, "last_success_at": None})
            current["dataset_count"] += row["dataset_count"]
            latest = row["last_success_at"]
            if latest and (current["last_success_at"] is None or latest > current["last_success_at"]):
                current["last_success_at"] = latest
        return sorted(sources.values(), key=lambda item: item["source"])

    def get_latest_bars(self, symbols, period="D"):
        if not symbols:
            return []
        placeholders = ",".join("?" for _ in symbols)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT k.symbol, k.period, k.trade_time, k.close, k.pct_chg, k.source
                FROM kline k
                JOIN (
                    SELECT symbol, period, MAX(trade_time) AS latest_time
                    FROM kline WHERE symbol IN ({placeholders}) AND period = ? GROUP BY symbol, period
                ) latest ON latest.symbol = k.symbol AND latest.period = k.period AND latest.latest_time = k.trade_time
                ORDER BY k.symbol
                """,
                [*symbols, period],
            ).fetchall()
        return [dict(row) for row in rows]

    def should_send_notification(self, notification_key, trade_time, state):
        trade_time = str(trade_time)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT trade_time, state FROM notification_state WHERE notification_key = ?", (notification_key,)
            ).fetchone()
            if row and row["trade_time"] == trade_time and row["state"] == state:
                return False
            connection.execute(
                """
                INSERT INTO notification_state(notification_key, trade_time, state, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(notification_key) DO UPDATE SET trade_time=excluded.trade_time,
                state=excluded.state, updated_at=excluded.updated_at
                """,
                (notification_key, trade_time, state, self._utc_now()),
            )
        return True
