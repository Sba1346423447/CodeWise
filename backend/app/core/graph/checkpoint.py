"""检查点持久化工厂：内存（默认/测试）↔ MySQL（生产持久化）。

多 Agent 改造三（生产化运行时）核心件：
- InMemorySaver：进程内 checkpoints，重启即失——测试与无 DB 环境零依赖默认
- AIOMySQLSaver：社区 langgraph-checkpoint-mysql 3.0（模仿官方 postgres saver
  实现，aiomysql 驱动与项目硬约束一致），检查点落 MySQL 后**重启不丢状态**，
  断点恢复 / time-travel 的持久化底座

模式选择：config/settings.yaml 的 agent.checkpointer（memory | mysql），
默认 memory（行为兼容：测试 / e2e / 无 MySQL 环境不受影响）。

连接复用 .env 的 DATABASE_URL（mysql+aiomysql://user:pw@host:port/db，
与业务库同实例同凭据，不引入第二个数据源）。
"""

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ...utils.logger import get_logger

logger = get_logger("core.graph.checkpoint")

# 项目根：checkpoint -> graph -> core -> app -> backend -> 根
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_CONFIG_PATH = _PROJECT_ROOT / "config" / "settings.yaml"


def _load_checkpointer_mode() -> str:
    """读取 settings.yaml 的 agent.checkpointer；缺省/异常回退 memory。"""
    try:
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        mode = (raw.get("agent", {}) or {}).get("checkpointer", "memory")
        return mode if mode in ("memory", "mysql") else "memory"
    except (OSError, yaml.YAMLError):
        return "memory"


# 模块级缓存（与 edges.py MAX_REFLECTION_ROUNDS 同模式）
CHECKPOINTER_MODE = _load_checkpointer_mode()


def _parse_database_url() -> dict[str, Any]:
    """解析 DATABASE_URL（mysql+aiomysql://user:pw@host:port/db）为连接参数。

    复用业务库连接串（main.py 已 load_dotenv，此处必能读到）。
    """
    import os

    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("checkpointer=mysql 需要 .env 配置 DATABASE_URL")
    parsed = urlparse(url)
    if "mysql" not in (parsed.scheme or ""):
        raise RuntimeError(f"checkpointer=mysql 需要 mysql 连接串，当前 scheme={parsed.scheme}")
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": parsed.username or "root",
        "password": parsed.password or "",
        "db": (parsed.path or "/codewise").lstrip("/"),
    }


def _patch_mysql_saver_collation() -> None:
    """修复社区包 MySQL 8 collation 兼容 bug（2026-09-09 实测定位）。

    包的 SELECT_SQL 中 json_table 生成列只声明 CHARACTER SET 未声明
    COLLATE，MySQL 8 下默认 utf8mb4_0900_ai_ci，与表列的
    utf8mb4_unicode_ci 在 JOIN（bl.channel = channel_versions.channel）
    时报 1267 Illegal mix of collations。

    修法：给生成列补 COLLATE utf8mb4_unicode_ci 对齐表列。patch 模块
    属性即可生效（_select_sql 运行时读模块全局 SELECT_SQL）；上游修复
    后（替换源串不命中）自动跳过，无升级冲突。
    """
    from langgraph.checkpoint.mysql import base as mysql_base

    broken = "columns (channel VARCHAR(150) CHARACTER SET utf8mb4 PATH '$')"
    fixed = ("columns (channel VARCHAR(150) CHARACTER SET utf8mb4 "
             "COLLATE utf8mb4_unicode_ci PATH '$')")
    if broken in mysql_base.SELECT_SQL:
        mysql_base.SELECT_SQL = mysql_base.SELECT_SQL.replace(broken, fixed)
        logger.info("已修复 langgraph-checkpoint-mysql 的 json_table collation 兼容问题")


async def create_mysql_saver():
    """建立 MySQL 连接并构造已初始化（setup 建表）的 AIOMySQLSaver。

    连接 autocommit=True（社区包要求：setup 的建表 DDL 需即时提交）。
    setup 幂等（内部维护 migrations 版本表），每次启动调用安全。

    collation 对齐（2026-09-09 实测坑，两处）：
    1. json_table 生成列默认 collation 0900_ai_ci vs 表列 unicode_ci
       → _patch_mysql_saver_collation 补 COLLATE（见上）
    2. 连接默认 collation general_ci 与参数比较路径不一致 → 连接后
       SET NAMES 对齐到表 collation（连接级设置，不动表不动包）
    """
    import aiomysql
    from langgraph.checkpoint.mysql.aio import AIOMySQLSaver

    _patch_mysql_saver_collation()
    params = _parse_database_url()
    conn = await aiomysql.connect(autocommit=True, **params)
    async with conn.cursor() as cur:
        await cur.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci")
    saver = AIOMySQLSaver(conn)
    await saver.setup()
    logger.info("MySQL 检查点持久化就绪 | host={} db={}", params["host"], params["db"])
    return saver


async def close_mysql_saver(saver) -> None:
    """关闭 saver 底层连接（应用 shutdown 时调用）。"""
    conn = getattr(saver, "conn", None)
    if conn is not None:
        await conn.ensure_closed()
        logger.info("MySQL 检查点连接已关闭")
