# A-Stock Signal Monitor

面向A股的全市场策略筛选、ETF分钟信号监控与Web可视化工具。行情统一经过 `market_data` 服务获取，并增量写入
`resource/market_data.db`，调用方无需关心当前数据来自网络还是SQLite缓存。股票和ETF主列表分别保存在
`stock_master`、`etf_master` 表中，自定义分组及组内置顶状态也由SQLite持久化。

## Web控制台

控制台包含监控总览、策略信号、股票主数据、ETF主数据、后台任务与扫描历史，支持深色和浅色模式。股票和ETF可以分别
创建自定义分组，以Tab切换，并支持组内置顶、移出和删除分组。策略指标由 `StockStatus` 动态生成。

```bash
uv sync
uv run a-stock-web
```

浏览器访问 `http://127.0.0.1:5000`。也可通过 `WEB_HOST` 和 `WEB_PORT` 调整监听地址与端口。

首次使用建议先在页面执行“刷新股票列表”和“更新ETF库”，再启动全市场扫描。扫描会将批次进度和命中信号写入SQLite，
页面可按指标、股票代码或名称筛选结果。旧版固定的6个ETF监控池暂时独立保留，不会写入新的ETF主列表。

## 数据源降级顺序

- 日线：BaoStock → 东方财富 → SQLite历史缓存。
- 分钟线：BaoStock → 东方财富 → 新浪 → SQLite历史缓存；过旧K线会继续触发下一个渠道。
- ETF列表：BaoStock → 东方财富 → SQLite最近快照。
- Tushare：非核心可选扩展，仅在安装可选依赖并设置 `ENABLE_TUSHARE_FALLBACK=1` 时作为日线最后网络兜底。

所有网络源均失败但本地存在历史记录时，服务返回最近缓存并将来源标记为 `sqlite_stale_cache`。

### 日线缓存策略

- 已覆盖最新完成交易日时直接读取SQLite，不再按固定TTL重复请求历史日线。
- 缺少新交易日时，仅请求最近两根已缓存日线至最新完成交易日，用于补充新数据和校验数据源修正。
- 如果重叠日线价格发生变化，视为除权除息引起的前复权历史变化，自动刷新当前策略所需的完整历史窗口。
- 盘中不会把当天尚未完成的日线视为正式缓存；默认在15:30后才检查当天日线。
- 分钟线仍保留短TTL，以保证ETF盘中监控的实时性。

## 使用

```bash
uv sync
uv run a-stock-refresh
uv run a-stock-refresh-etfs
uv run a-stock-scan
uv run a-stock-monitor
uv run a-stock-web
```

项目依赖由 `pyproject.toml` 和 `uv.lock` 统一管理，虚拟环境固定使用项目根目录下的 `.venv`。
如需启用可选的Tushare兜底，可执行 `uv sync --extra tushare`。

可通过环境变量覆盖数据库位置、请求超时和重试次数：

```bash
export MARKET_DATA_DB=/path/to/market_data.db
export MARKET_DATA_HTTP_TIMEOUT=10
export MARKET_DATA_MAX_RETRIES=2
export MIN_STOCK_LIST_SIZE=1000
export MIN_ETF_LIST_SIZE=100
export DAILY_CACHE_OVERLAP_BARS=2
```

东方财富默认只使用HTTPS。如所在网络只能访问其HTTP接口，可显式设置
`ALLOW_INSECURE_HTTP_FALLBACK=1`，但不建议在不可信网络中开启。

## 架构

- `market_data/providers`：各数据源的协议适配与字段标准化。
- `market_data/service.py`：数据源降级、有限重试、增量获取和缓存回退。
- `market_data/database.py`：K线、股票/ETF主数据、自定义分组、交易日历和通知去重状态。
- `stock_signal_monitor/stock_strategy.py`：只处理策略计算，不直接依赖某个行情SDK。
- `etf_monitor`：按K线收盘时间调度均线监控。
- `web_app`：Flask API、后台任务入口和响应式Web控制台。
