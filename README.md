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

### 数据库目录与OneDrive同步

“系统设置 → 数据库存储”可以修改 `market_data.db` 所在目录。目标目录尚无数据库时，
可通过SQLite在线备份将当前历史K线、
股票/ETF主数据、自定义分组和任务记录完整复制过去；目标已有同名数据库时不会覆盖，而是直接切换使用。
每台设备的本机路径保存在
`resource/app_settings.json`，该文件已被Git忽略。

放入OneDrive等云盘时建议开启“云盘兼容模式”，它会使用SQLite `DELETE` 日志模式，避免持久化的 `-wal`、`-shm` 文件参与同步。
云盘同步不是数据库复制协议：同一时刻只能有一台设备运行本项目。切换设备前应先关闭项目，
等待数据库同步完成后再启动另一台设备。

如需通过部署环境统一管理，也可设置完整文件路径 `MARKET_DATA_DB`。环境变量优先级高于网页设置，启用后网页只读。

## 数据源降级顺序

- A股日线：BaoStock → 新浪 → 腾讯财经 → 东方财富 → Tushare（配置Token后）→ SQLite历史缓存。
- 海外指数与黄金：Yahoo Finance / 上海黄金交易所专用源 → 其他兼容源 → SQLite历史缓存。
- 分钟线：BaoStock → 东方财富 → 新浪 → SQLite历史缓存；过旧K线会继续触发下一个渠道。
- ETF列表：BaoStock → 东方财富 → SQLite最近快照。
- Tushare：仅调用120积分可用的未复权股票日线，两个Token默认共用45次/分钟、7500次/日的保守额度。

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

Tushare Token只能写入已被Git忽略的 `resource/local_secrets.json`，可复制
`resource/local_secrets.example.json` 后填写；也可以使用环境变量，配置后会自动启用最后兜底：

```bash
export TUSHARE_TOKENS=第一个Token,第二个Token
export TUSHARE_REQUESTS_PER_MINUTE=45
export TUSHARE_DAILY_REQUEST_LIMIT=7500
```

可通过环境变量覆盖数据库位置、请求超时和重试次数：

```bash
export MARKET_DATA_DB=/path/to/market_data.db
export MARKET_DATA_HTTP_TIMEOUT=10
export MARKET_DATA_MAX_RETRIES=2
export MIN_STOCK_LIST_SIZE=1000
export MIN_ETF_LIST_SIZE=100
export DAILY_CACHE_OVERLAP_BARS=2
```

分钟K线保留时间统一在“系统设置 → 数据维护”中设置。历史日线不会自动删除；
保存设置和Web服务启动时只清理超过保留天数的分钟K线，避免盘中监控缓存无限增长。

东方财富默认只使用HTTPS。如所在网络只能访问其HTTP接口，可显式设置
`ALLOW_INSECURE_HTTP_FALLBACK=1`，但不建议在不可信网络中开启。

## 架构

- `market_data/providers`：各数据源的协议适配与字段标准化。
- `market_data/service.py`：数据源降级、有限重试、增量获取和缓存回退。
- `market_data/database.py`：K线、股票/ETF主数据、自定义分组、交易日历和任务记录。
- `market_data/interfaces.py`：行情Provider和日线读取服务的类型契约。
- `stock_signal_monitor/stock_strategy.py`：只处理策略计算，不直接依赖某个行情SDK。
- `web_app/index_service.py`：首页主要指数缓存、刷新冷却和历史分页。
- `etf_monitor`：按K线收盘时间调度均线监控。
- `web_app`：Flask API、后台任务入口和响应式Web控制台。
