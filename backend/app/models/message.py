"""消息模型：messages 表的数据访问层（持久化会话内多轮对话消息，支持回放）。

字段与 SSE 契约严格对齐：
- role：user | assistant（仅持久化对话正文，思考/工具过程不入此表，保证对话流干净）
- content：对话正文（含 Markdown/代码），由前端 Markdown 渲染
- thinking：可选，助手消息附带的本轮"行动摘要" JSON 字符串，供前端折叠展示
"""

import json
import uuid

from .database import get_connection

# 消息角色常量
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


async def add_message(
    session_id: str,
    role: str,
    content: str,
    thinking: list | dict | None = None,
) -> dict:
    """新增一条会话消息，返回完整消息记录。

    thinking 为可选的行动摘要（历史为 [{type, label, detail}] 列表；
    新版打包为 {steps, code, tests_passed, ...} 字典，供前端回放还原结构化组件），
    统一序列化为 JSON 存储；无摘要时传 None，表中存 NULL。
    """
    message_id = uuid.uuid4().hex
    thinking_json = json.dumps(thinking, ensure_ascii=False) if thinking else None
    conn = await get_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO messages (message_id, session_id, role, content, thinking) "
                "VALUES (%s, %s, %s, %s, %s)",
                (message_id, session_id, role, content, thinking_json),
            )
        await conn.commit()
    finally:
        conn.close()
    return {
        "message_id": message_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "thinking": thinking,
    }


async def get_messages_by_session(session_id: str) -> list[dict]:
    """查询某会话的全部消息，按创建时间升序（对话顺序），thinking 反序列化为列表。"""
    conn = await get_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM messages WHERE session_id = %s ORDER BY created_at ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
        return [
            {**row, "thinking": json.loads(row["thinking"]) if row["thinking"] else None}
            for row in rows
        ]
    finally:
        conn.close()


async def clear_messages(session_id: str) -> None:
    """清空某会话的全部消息（新建会话或重置会话时调用）。"""
    conn = await get_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("DELETE FROM messages WHERE session_id = %s", (session_id,))
        await conn.commit()
    finally:
        conn.close()
