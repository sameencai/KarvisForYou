# -*- coding: utf-8 -*-
"""
Karvis 统一日志模块
- 使用 Python 标准 logging
- 按天切换日志文件（TimedRotatingFileHandler）
- 统一格式：日期 时间 [文件名:行号] [请求ID] 消息
- 同时输出到 stderr（容器友好）和文件
"""
import os
import sys
import logging
import threading
from logging.handlers import TimedRotatingFileHandler
from datetime import timezone, timedelta

# ============ 配置 ============
LOG_DIR = os.environ.get("KARVIS_LOG_DIR", os.path.join(os.path.dirname(__file__), "logs"))
LOG_LEVEL = os.environ.get("KARVIS_LOG_LEVEL", "INFO").upper()
LOG_RETENTION_DAYS = int(os.environ.get("KARVIS_LOG_RETENTION_DAYS", "30"))

# 确保日志目录存在
os.makedirs(LOG_DIR, exist_ok=True)

# ============ 格式化器 ============
_LOG_FORMAT = "%(asctime)s [%(filename)s:%(lineno)d] %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _BeijingFormatter(logging.Formatter):
    """强制使用北京时间的格式化器"""
    _BEIJING_TZ = timezone(timedelta(hours=8))

    def formatTime(self, record, datefmt=None):
        from datetime import datetime
        ct = datetime.fromtimestamp(record.created, tz=self._BEIJING_TZ)
        if datefmt:
            return ct.strftime(datefmt)
        return ct.strftime(_LOG_DATE_FORMAT)


# ============ 创建 Logger ============
_logger = logging.getLogger("karvis")
_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_logger.propagate = False  # 不传播到 root logger

# Handler: 按天轮转文件
_file_handler = TimedRotatingFileHandler(
    filename=os.path.join(LOG_DIR, "karvis.log"),
    when="midnight",
    interval=1,
    backupCount=LOG_RETENTION_DAYS,
    encoding="utf-8",
    atTime=None,
)
_file_handler.suffix = "%Y-%m-%d"  # 归档文件名后缀: karvis.log.2026-05-20
_file_handler.setFormatter(_BeijingFormatter(_LOG_FORMAT, _LOG_DATE_FORMAT))
_logger.addHandler(_file_handler)

# Handler: 同时输出到 stderr（方便 docker logs / 实时查看）
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(_BeijingFormatter(_LOG_FORMAT, _LOG_DATE_FORMAT))
_logger.addHandler(_stderr_handler)


# ============ Request ID 支持 ============
_thread_local = threading.local()


def set_request_id(rid=None):
    """设置当前线程的请求 ID"""
    import uuid
    _thread_local.request_id = rid or uuid.uuid4().hex[:8]
    return _thread_local.request_id


def get_request_id():
    """获取当前线程的请求 ID"""
    return getattr(_thread_local, "request_id", None)


# ============ 统一日志函数 ============
def log(msg, level="info"):
    """
    统一日志输出入口。
    自动附加请求 ID（如有）。
    
    用法：
        from logger import log
        log("消息内容")
        log("错误信息", level="error")
    """
    rid = get_request_id()
    if rid:
        full_msg = f"[{rid}] {msg}"
    else:
        full_msg = msg

    # 使用 stacklevel=2 让 logging 记录调用者的文件名和行号
    log_fn = getattr(_logger, level.lower(), _logger.info)
    log_fn(full_msg, stacklevel=2)


# 便捷别名
def info(msg):
    log(msg, "info")

def warn(msg):
    log(msg, "warning")

def error(msg):
    log(msg, "error")

def debug(msg):
    log(msg, "debug")
