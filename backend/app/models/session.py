"""会话模型：sessions 表的数据访问层（创建 / 查询 / 更新 / 删除）。

对外提供异步 CRUD 接口，供 API 层与编排器调用；连接生命周期在函数内自管理。
"""

import uuid
from typing import Dict, List, Optional

from .database import get_connection

# 会话状态常量
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
# 用户手动停止：Agent 执行中途被前端"停止生成"按钮中断
STATUS_STOPPED = "stopped"
# 安全审查挂起：confirm_node interrupt 等待用户批准/拒绝（human-in-the-loop）
STATUS_AWAITING = "awaiting_confirmation"


async def create_session(task_desc: str) -> Dict:
    """创建新会话（初始状态 running），返回完整会话记录。"""
    session_id = uuid.uuid4().hex
    conn = await get_connection()
    try:
        await conn.execute(
            "INSERT INTO sessions (session_id, task_desc) VALUES (?, ?)",
            (session_id, task_desc),
        )
        await conn.commit()
    finally:
        await conn.close()

    session = await get_session(session_id)
    return session or {}


async def get_session(session_id: str) -> Optional[Dict]:
    """按 ID 查询会话；不存在返回 None。"""
    conn = await get_connection()
    try:
        async with conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
    finally:
        await conn.close()


async def list_sessions() -> List[Dict]:
    """列出全部会话，按创建时间倒序（最新在前）。"""
    conn = await get_connection()
    try:
        async with conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await conn.close()


async def update_session(
    session_id: str,
    status: Optional[str] = None,
    final_code: Optional[str] = None,
) -> None:
    """更新会话状态与最终代码（仅更新传入的非 None 字段）。"""
    conn = await get_connection()
    try:
        if status is not None:
            await conn.execute(
                "UPDATE sessions SET status = ? WHERE session_id = ?",
                (status, session_id),
            )
        if final_code is not None:
            await conn.execute(
                "UPDATE sessions SET final_code = ? WHERE session_id = ?",
                (final_code, session_id),
            )
        await conn.commit()
    finally:
        await conn.close()


async def rename_session(session_id: str, task_desc: str) -> None:
    """重命名会话（更新任务描述，供前端标题栏重命名操作）。"""
    conn = await get_connection()
    try:
        await conn.execute(
            "UPDATE sessions SET task_desc = ? WHERE session_id = ?",
            (task_desc, session_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def delete_session(session_id: str) -> None:
    """删除会话及其关联数据（先删子表再删主表，绕过 SQLite 无级联定义）。

    messages 表同样外键引用 sessions，必须先删，否则外键约束会导致删除失败。
    """
    conn = await get_connection()
    try:
        await conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        await conn.execute("DELETE FROM steps WHERE session_id = ?", (session_id,))
        await conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        await conn.commit()
    finally:
        await conn.close()
