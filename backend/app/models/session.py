"""会话模型：sessions 表的数据访问层（创建 / 查询 / 更新 / 删除）。

对外提供异步 CRUD 接口，供 API 层与编排器调用；连接生命周期在函数内自管理。
"""

import uuid

from .database import get_connection

# 会话状态常量
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
# 用户手动停止：Agent 执行中途被前端"停止生成"按钮中断
STATUS_STOPPED = "stopped"
# 安全审查挂起：confirm_node interrupt 等待用户批准/拒绝（human-in-the-loop）
STATUS_AWAITING = "awaiting_confirmation"


async def create_session(task_desc: str) -> dict:
    """创建新会话（初始状态 running），返回完整会话记录。"""
    session_id = uuid.uuid4().hex
    conn = await get_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO sessions (session_id, task_desc) VALUES (%s, %s)",
                (session_id, task_desc),
            )
        await conn.commit()
    finally:
        conn.close()

    session = await get_session(session_id)
    return session or {}


async def get_session(session_id: str) -> dict | None:
    """按 ID 查询会话；不存在返回 None。"""
    conn = await get_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM sessions WHERE session_id = %s", (session_id,)
            )
            return await cursor.fetchone()
    finally:
        conn.close()


async def list_sessions() -> list[dict]:
    """列出全部会话，按创建时间倒序（最新在前）。"""
    conn = await get_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT * FROM sessions ORDER BY created_at DESC")
            return list(await cursor.fetchall())
    finally:
        conn.close()


async def update_session(
    session_id: str,
    status: str | None = None,
    final_code: str | None = None,
) -> None:
    """更新会话状态与最终代码（仅更新传入的非 None 字段）。"""
    conn = await get_connection()
    try:
        async with conn.cursor() as cursor:
            if status is not None:
                await cursor.execute(
                    "UPDATE sessions SET status = %s WHERE session_id = %s",
                    (status, session_id),
                )
            if final_code is not None:
                await cursor.execute(
                    "UPDATE sessions SET final_code = %s WHERE session_id = %s",
                    (final_code, session_id),
                )
        await conn.commit()
    finally:
        conn.close()


async def rename_session(session_id: str, task_desc: str) -> None:
    """重命名会话（更新任务描述，供前端标题栏重命名操作）。"""
    conn = await get_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE sessions SET task_desc = %s WHERE session_id = %s",
                (task_desc, session_id),
            )
        await conn.commit()
    finally:
        conn.close()


async def delete_session(session_id: str) -> None:
    """删除会话及其关联数据（先删子表再删主表，满足外键约束）。"""
    conn = await get_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("DELETE FROM messages WHERE session_id = %s", (session_id,))
            await cursor.execute("DELETE FROM steps WHERE session_id = %s", (session_id,))
            await cursor.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
        await conn.commit()
    finally:
        conn.close()
