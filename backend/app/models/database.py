"""数据库连接与迁移：MySQL（aiomysql 异步驱动），schema 由 Alembic 管理。

依赖：aiomysql（异步 MySQL 驱动，附带 PyMySQL 同步驱动供 Alembic 使用）、alembic（迁移）。
连接串由环境变量 DATABASE_URL 指定，格式：mysql+aiomysql://user:pass@host:port/dbname。

模型层 SQL 统一使用 MySQL 方言（占位符 %s）；建表/变更不写在代码里，
全部走 alembic/versions/ 迁移脚本，init_db() 在启动时幂等执行 upgrade head。
"""

import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

import aiomysql

# 连接串：环境变量注入（backend/.env 或 compose environment），无默认值时连本地
_DATABASE_URL = os.getenv(
    "DATABASE_URL", "mysql+aiomysql://root@127.0.0.1:3306/codewise"
)

# Alembic 迁移脚本目录（backend/alembic，与 app 包同级）
_ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"


def _parse_url(url: str) -> dict:
    """解析连接串为 aiomysql.connect 参数（密码做 URL 反转义）。"""
    parts = urlsplit(url)
    return {
        "host": parts.hostname or "127.0.0.1",
        "port": parts.port or 3306,
        "user": unquote(parts.username) if parts.username else "root",
        "password": unquote(parts.password) if parts.password else None,
        "db": parts.path.lstrip("/"),
    }


async def get_connection() -> aiomysql.Connection:
    """创建异步 MySQL 连接（DictCursor，查询直接返回 dict 行；由调用方关闭）。"""
    return await aiomysql.connect(
        **_parse_url(_DATABASE_URL),
        cursorclass=aiomysql.DictCursor,
        autocommit=False,
        charset="utf8mb4",
    )


def init_db() -> None:
    """初始化数据库：确保库存在并执行 alembic upgrade head（幂等，同步阻塞仅启动时一次）。"""
    import pymysql
    from alembic.config import Config

    from alembic import command

    params = _parse_url(_DATABASE_URL)

    # 库不存在时创建（连接不指定 db）
    conn = pymysql.connect(
        host=params["host"], port=params["port"],
        user=params["user"], password=params["password"],
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{params['db']}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()

    # 执行迁移：同步 PyMySQL 驱动（aiomysql 自带依赖），脚本位置指向 backend/alembic
    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option(
        "sqlalchemy.url", _DATABASE_URL.replace("+aiomysql", "+pymysql")
    )
    command.upgrade(cfg, "head")
