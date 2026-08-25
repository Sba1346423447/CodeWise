"""pytest 全局配置：sys.path 注入、测试库连接串、schema 初始化与数据隔离。

- ChromaDB 指向未监听端口，使 ExperienceStore 导入时立即降级为空库，避免测试阻塞。
- 测试统一连 MySQL 测试库（codewise_test）：连接串取环境变量 TEST_DATABASE_URL
  （CI 注入，或 backend/.env 中本地配置），必须在 import app 之前写入 DATABASE_URL
  （main.py 的 load_dotenv 不覆盖已存在的环境变量）。
- db_schema（session 级）执行 alembic upgrade head 建表；clear_tables（函数级）
  清空三表保证用例隔离，仅 DB 相关测试引用，纯单元测试不依赖 MySQL。
"""

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

# backend 根目录（conftest.py 位于 backend/tests/，上溯一层）
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# 测试环境不依赖外部 ChromaDB 服务：指向未监听端口，让 ExperienceStore
# 在导入时立即连接失败并降级为空库（见 experience_store 的容错设计），
# 保证测试不因网络等待而阻塞。
os.environ.setdefault("CHROMA_HOST", "127.0.0.1")
os.environ.setdefault("CHROMA_PORT", "59999")

# 测试库连接串：CI 环境变量优先；本地从 backend/.env 读取（含密码不入库）
load_dotenv(_BACKEND_ROOT / ".env")
_test_url = os.getenv("TEST_DATABASE_URL")
if _test_url:
    os.environ["DATABASE_URL"] = _test_url


@pytest.fixture(scope="session")
def db_schema():
    """会话级建表：执行 alembic upgrade head（幂等），供 DB 相关测试依赖。"""
    from app.models.database import init_db

    init_db()


@pytest.fixture
async def clear_tables(db_schema):
    """清空三张表保证用例间数据隔离（测试库专用，不触碰开发库）。"""
    from app.models import database

    conn = await database.get_connection()
    try:
        async with conn.cursor() as cursor:
            for table in ("messages", "steps", "sessions"):
                await cursor.execute(f"DELETE FROM {table}")
        await conn.commit()
    finally:
        conn.close()
