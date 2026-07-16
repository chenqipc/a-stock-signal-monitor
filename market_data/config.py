"""项目级路径和行情数据配置。"""

import json
import os
from pathlib import Path
from threading import RLock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESOURCE_DIR = PROJECT_ROOT / "resource"
DEFAULT_DATABASE_DIRECTORY = RESOURCE_DIR
DATABASE_FILENAME = "market_data.db"
APP_SETTINGS_PATH = RESOURCE_DIR / "app_settings.json"
LOCAL_SECRETS_PATH = RESOURCE_DIR / "local_secrets.json"
_SETTINGS_LOCK = RLock()
ENABLE_TUSHARE_FALLBACK = os.getenv("ENABLE_TUSHARE_FALLBACK", "0") == "1"
TUSHARE_REQUESTS_PER_MINUTE = max(1, int(os.getenv("TUSHARE_REQUESTS_PER_MINUTE", "45")))
TUSHARE_DAILY_REQUEST_LIMIT = max(1, int(os.getenv("TUSHARE_DAILY_REQUEST_LIMIT", "7500")))
HTTP_TIMEOUT_SECONDS = float(os.getenv("MARKET_DATA_HTTP_TIMEOUT", "10"))
PROVIDER_MAX_RETRIES = int(os.getenv("MARKET_DATA_MAX_RETRIES", "2"))
PROVIDER_RETRY_MAX_DELAY_SECONDS = int(os.getenv("MARKET_DATA_RETRY_MAX_DELAY_SECONDS", "60"))
ALLOW_INSECURE_HTTP_FALLBACK = os.getenv("ALLOW_INSECURE_HTTP_FALLBACK", "0") == "1"
DAILY_CACHE_OVERLAP_BARS = max(2, int(os.getenv("DAILY_CACHE_OVERLAP_BARS", "2")))
MINUTE_CACHE_TTL_SECONDS = int(os.getenv("MINUTE_CACHE_TTL_SECONDS", "60"))
MIN_STOCK_LIST_SIZE = int(os.getenv("MIN_STOCK_LIST_SIZE", "1000"))
MIN_ETF_LIST_SIZE = int(os.getenv("MIN_ETF_LIST_SIZE", "100"))
SCAN_CACHED_WORKERS = max(1, int(os.getenv("SCAN_CACHED_WORKERS", "6")))


def get_tushare_tokens(secrets_path=None):
    """从环境变量或本机私密文件读取Token，绝不使用可提交的Python源码保存密钥。"""
    environment_value = os.getenv("TUSHARE_TOKENS") or os.getenv("TUSHARE_TOKEN")
    if environment_value:
        return _normalize_tokens(environment_value)
    path = Path(secrets_path or LOCAL_SECRETS_PATH)
    if not path.exists():
        return []
    with _SETTINGS_LOCK:
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"本机私密配置无法读取: {path}") from exc
    values = content.get("tushare_tokens", []) if isinstance(content, dict) else []
    return _normalize_tokens(values)


def _normalize_tokens(values):
    if isinstance(values, str):
        values = values.replace(";", ",").replace("\n", ",").split(",")
    if not isinstance(values, (list, tuple)):
        return []
    # 去重时保持配置顺序，便于Token轮换行为稳定且可预测。
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def get_database_configuration(settings_path=None):
    """读取数据库目录配置；环境变量优先且不允许被网页设置覆盖。"""
    environment_path = os.getenv("MARKET_DATA_DB")
    if environment_path:
        database_path = Path(environment_path).expanduser().resolve()
        return {
            "database_directory": str(database_path.parent),
            "database_path": str(database_path),
            "cloud_sync_mode": False,
            "managed_by_environment": True,
        }
    settings = _load_settings(settings_path)
    directory = Path(settings.get("database_directory") or DEFAULT_DATABASE_DIRECTORY).expanduser().resolve()
    database_path = directory / DATABASE_FILENAME
    return {
        "database_directory": str(directory),
        "database_path": str(database_path),
        "cloud_sync_mode": bool(settings.get("cloud_sync_mode", False)),
        "managed_by_environment": False,
    }


def get_database_path(settings_path=None):
    return Path(get_database_configuration(settings_path)["database_path"])


def get_database_journal_mode(settings_path=None):
    configuration = get_database_configuration(settings_path)
    return "DELETE" if configuration["cloud_sync_mode"] else "WAL"


def save_database_configuration(database_directory, cloud_sync_mode=False, settings_path=None):
    """将本机数据库目录写入独立设置文件，避免把个人云盘路径提交到Git。"""
    if os.getenv("MARKET_DATA_DB"):
        raise ValueError("数据库路径由 MARKET_DATA_DB 环境变量管理，无法在网页中修改")
    directory = Path(str(database_directory or "").strip()).expanduser()
    if not directory.is_absolute():
        raise ValueError("数据库目录必须是绝对路径")
    directory = directory.resolve()
    path = Path(settings_path or APP_SETTINGS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"database_directory": str(directory), "cloud_sync_mode": bool(cloud_sync_mode)}
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with _SETTINGS_LOCK:
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(path)
    return get_database_configuration(path)


def _load_settings(settings_path=None):
    path = Path(settings_path or APP_SETTINGS_PATH)
    if not path.exists():
        return {}
    with _SETTINGS_LOCK:
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"应用设置文件无法读取: {path}") from exc
    return content if isinstance(content, dict) else {}


# 兼容已有导入；新建服务应调用 get_database_path() 以读取运行时设置。
DATABASE_PATH = get_database_path()
