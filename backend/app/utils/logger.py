"""日志配置：loguru 结构化日志，控制台 + 文件双输出，按日轮转。

依赖：loguru（结构化日志），环境变量 LOG_LEVEL / LOG_DIR 可覆盖默认行为。
"""

import os
import sys

from loguru import logger

# 全局日志级别，环境变量 LOG_LEVEL 可覆盖，默认 INFO
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# 日志目录，环境变量 LOG_DIR 可覆盖，默认 backend/logs
LOG_DIR = os.getenv("LOG_DIR", "logs")

_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{extra[module]}</cyan> | "
    "<level>{message}</level>"
)


def setup_logging() -> None:
    """初始化全局日志：控制台 + 文件双输出，文件按日轮转、保留 7 天。

    remove() 先移除默认 handler，保证重复调用不产生重复输出。
    """
    logger.remove()

    # 控制台：彩色结构化输出
    logger.add(sys.stderr, level=LOG_LEVEL, format=_FORMAT, enqueue=True)

    # 文件：按日轮转，utf-8 编码，保留 7 天
    os.makedirs(LOG_DIR, exist_ok=True)
    logger.add(
        os.path.join(LOG_DIR, "codewise_{time:YYYY-MM-DD}.log"),
        level=LOG_LEVEL,
        format=_FORMAT,
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
        enqueue=True,
    )


def get_logger(name: str):
    """绑定模块名的 logger：记录注入 extra[module]，便于定位日志来源。"""
    return logger.patch(lambda record: record["extra"].setdefault("module", name))
