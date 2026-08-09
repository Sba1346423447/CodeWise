"""数据库初始化：创建 SQLite 连接、建表语句与迁移逻辑。

依赖：aiosqlite（异步 SQLite 驱动）；数据库路径由环境变量 DATABASE_URL 指定，
支持 sqlite+aiosqlite:/// 前缀（与 SQLAlchemy 连接串兼容）。
"""

import os
from typing import Optional

import aiosqlite

# 数据库文件路径，环境变量 DATABASE_URL 可覆盖（支持 sqlite+aiosqlite:/// 前缀）
_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./codewise.db")
_DB_PATH = _DATABASE_URL.replace("sqlite+aiosqlite:///", "")

# 当前 schema 版本（通过 PRAGMA user_version 跟踪）
SCHEMA_VERSION = 2

# v1 建表语句：sessions（会话）+ steps（执行步骤），外键关联
_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    task_desc  TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'running',
    final_code TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS steps (
    step_id    TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    step_type  TEXT NOT NULL,
    input      TEXT,
    output     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
"""

# v2 增量：messages 表（会话内多轮对话消息持久化，支持回放）。
# role 取值 user | assistant；thinking 存行动摘要 JSON，可空。
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    thinking   TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
"""

# 迁移表：目标版本 -> 需执行的 SQL（版本递增时追加新条目）
_MIGRATIONS = {
    1: _SCHEMA_V1,
    2: _SCHEMA_V2,
}


async def get_connection() -> aiosqlite.Connection:
    """创建异步 SQLite 连接，启用行工厂与外键约束（用完由调用方关闭）。"""
    conn = await aiosqlite.connect(_DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn


async def init_db() -> None:
    """初始化数据库：按版本逐级执行迁移，保证 schema 与代码版本一致。"""
    conn = await get_connection()
    try:
        # 读取当前数据库版本
        async with conn.execute("PRAGMA user_version") as cursor:
            row = await cursor.fetchone()
            current_version = row[0] if row else 0

        # 从当前版本逐级升级到目标版本（支持未来增量迁移）
        for version in range(current_version + 1, SCHEMA_VERSION + 1):
            sql = _MIGRATIONS.get(version)
            if sql:
                await conn.executescript(sql)
            await conn.execute(f"PRAGMA user_version = {version}")

        await conn.commit()
    finally:
        await conn.close()
