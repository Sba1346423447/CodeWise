"""Alembic 迁移环境：同步 PyMySQL 驱动，连接串取自环境变量 DATABASE_URL。

迁移脚本使用原生 SQL（op.execute），不绑定 ORM metadata（本项目模型层为裸 SQL）。
"""

import os
from logging.config import fileConfig

from sqlalchemy import create_engine

from alembic import context

# alembic.ini 中的日志配置
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 连接串优先级：环境变量 DATABASE_URL（mysql+aiomysql:// 前缀换同步驱动）> alembic.ini
_url = os.getenv(
    "DATABASE_URL", config.get_main_option("sqlalchemy.url")
).replace("+aiomysql", "+pymysql")


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL 不执行（本项目未使用，保留 Alembic 标准入口）。"""
    context.configure(url=_url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直连数据库执行迁移。"""
    engine = create_engine(_url, pool_pre_ping=True)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
