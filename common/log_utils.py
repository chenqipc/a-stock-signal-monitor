import os
import logging
from logging.handlers import RotatingFileHandler

class LoggerManager:
    """
    日志管理工具类，用于为不同的股票代码创建独立的日志记录器
    """
    
    # 日志记录器缓存
    _loggers = {}
    
    @classmethod
    def setup_log_dir(cls):
        """设置日志目录"""
        # 统一写入项目根目录，避免重构前后出现两套日志目录。
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
        os.makedirs(log_dir, exist_ok=True)
        return log_dir
    
    @classmethod
    def get_logger(cls, logger_name):
        """
        获取指定名称的日志记录器
        
        参数：
            logger_name: 日志记录器名称，通常是股票代码_中文名称
        """
        if logger_name in cls._loggers:
            return cls._loggers[logger_name]
        
        # 创建新的日志记录器
        logger = logging.getLogger(f"a_stock_signal_monitor_{logger_name}")
        logger.setLevel(logging.INFO)
        
        # 防止日志重复输出
        if not logger.handlers:
            # 创建日志目录
            log_dir = cls.setup_log_dir()
            
            # 创建文件处理器，使用 RotatingFileHandler 限制文件大小
            # 使用 logger_name 作为文件名，确保包含中文名称
            log_file = os.path.join(log_dir, f"{logger_name}.log")
            file_handler = RotatingFileHandler(
                log_file, 
                maxBytes=10*1024*1024,  # 10MB
                backupCount=5,
                encoding='utf-8'  # 确保支持中文
            )
            
            # 设置日志格式
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(formatter)
            
            # 添加处理器到日志记录器
            logger.addHandler(file_handler)
            
            # 同时输出到控制台
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
            logger.propagate = False
        
        # 缓存日志记录器
        cls._loggers[logger_name] = logger
        return logger
