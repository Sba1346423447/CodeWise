"""步骤模型：steps 表的数据访问层（记录 Agent 每个执行步骤的输入输出）。

步骤类型与图节点一一对应，供前端执行时间线渲染使用。
"""

import uuid
from typing import Dict, List, Optional

from .database import get_connection

# 步骤类型常量（与图节点一一对应）
STEP_REACT = "react"
STEP_TOOL = "tool"
STEP_REFLECT = "reflect"
STEP_REFINE = "refine"


async def create_step(
    session_id: str,
    step_type: str,
    input_data: str = "",
    output_data: str = "",
) -> Dict:
    """记录一个执行步骤，返回完整步骤记录。"""
    step_id = uuid.uuid4().hex
    conn = await get_connection()
    try:
        await conn.execute(
            "INSERT INTO steps (step_id, session_id, step_type, input, output) "
            "VALUES (?, ?, ?, ?, ?)",
            (step_id, session_id, step_type, input_data, output_data),
        )
        await conn.commit()
    finally:
        await conn.close()
    return {"step_id": step_id, "session_id": session_id, "step_type": step_type,
            "input": input_data, "output": output_data}


async def get_steps_by_session(session_id: str) -> List[Dict]:
    """查询某会话的全部步骤，按创建时间升序（执行顺序）。"""
    conn = await get_connection()
    try:
        async with conn.execute(
            "SELECT * FROM steps WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await conn.close()


async def get_step(step_id: str) -> Optional[Dict]:
    """按 ID 查询单个步骤；不存在返回 None。"""
    conn = await get_connection()
    try:
        async with conn.execute(
            "SELECT * FROM steps WHERE step_id = ?", (step_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
    finally:
        await conn.close()
