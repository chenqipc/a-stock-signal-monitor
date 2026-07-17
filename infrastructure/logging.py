"""项目日志配置与按标的滚动文件日志。"""

import logging
from logging.handlers import RotatingFileHandler

from market_data.config import PROJECT_ROOT


def configure_application_logging(level=logging.INFO):
    """为CLI和Web后台任务提供统一控制台格式，已配置时不重复添加Handler。"""
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    elif root_logger.level > level:
        root_logger.setLevel(level)


class LoggerManager:
    """为分钟监控提供控制台与按标的滚动文件日志。"""

    _loggers = {}

    @classmethod
    def setup_log_dir(cls):
        log_dir = PROJECT_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    @classmethod
    def get_logger(cls, logger_name):
        if logger_name in cls._loggers:
            return cls._loggers[logger_name]
        logger = logging.getLogger(f"a_stock_signal_monitor_{logger_name}")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            file_handler = RotatingFileHandler(
                cls.setup_log_dir() / f"{logger_name}.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
            logger.propagate = False
        cls._loggers[logger_name] = logger
        return logger
