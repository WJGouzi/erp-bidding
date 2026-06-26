import logging
import logging.handlers
import os
import sys
from pathlib import Path


def setup_logging(log_dir=None, log_level="DEBUG", console_level="INFO"):
    """配置 Python logging 系统，统一项目内日志格式。
    
    日志格式: 2026-06-15 14:30:00 [INFO] module:function:line - message
    日志级别: DEBUG < INFO < WARNING < ERROR
    
    Args:
        log_dir: 日志文件目录，默认 ./logs
        log_level: 文件日志级别
        console_level: 控制台日志级别
    """
    
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    
    # 清除已有 handlers 避免重复注册
    for item in list(root.handlers):
        root.removeHandler(item)
    
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(name)s:%(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, console_level.upper(), logging.INFO))
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)
    
    # 文件 handler（带轮转 10MB）
    log_path = Path(log_dir or "logs")
    log_path.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(log_path / "app.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(getattr(logging, log_level.upper(), logging.DEBUG))
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    
    # 第三方库的日志调低，避免刷屏
    for noisy in ("sqlalchemy.engine", "urllib3", "chromadb"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    
    logging.getLogger("app").info("日志系统初始化完成, 文件路径: %s", log_path / "app.log")
    return logging.getLogger("app")
