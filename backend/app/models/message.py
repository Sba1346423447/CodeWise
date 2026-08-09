"""消息模型：messages 表的数据访问层（持久化会话内多轮对话消息，支持回放）。

对应 database.py 的 schema v2 中 messages 表，字段与后端 SSE 契约严格对齐：
- role：user | assistant（仅持久化对话正文，思考/工具过程不入此表，保证对话流干净）
- content：对话正文（含 Markdown/代码），由前端 Markdown 渲染
- thinking：可选，助手消息附带的本轮"行动摘要" JSON 字符串，供前端折叠展示
"""

import json
import uuid
from typing import Dict, List, Optional, Union

from .database import get_connection

# 消息角色常量
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


async def add_message(
    session_id: str,
    role: str,
    content: str,
    thinking: Optional[Union[list, dict]] = None,
) -> Dict:
    """新增一条会话消息，返回完整消息记录。

    thinking 为可选的行动摘要（历史为 [{type, label, detail}] 列表；
    新版打包为 {steps, code, tests_passed, ...} 字典，供前端回放还原结构化组件），
    统一序列化为 JSON 存储；无摘要时传 None，表中存 NULL。
    """
    message_id = uuid.uuid4().hex
    thinking_json = json.dumps(thinking, ensure_ascii=False) if thinking else None
    conn = await get_connection()
    try:
        await conn.execute(
            "INSERT INTO messages (message_id, session_id, role, content, thinking) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, session_id, role, content, thinking_json),
        )
        await conn.commit()
    finally:
        await conn.close()
    return {
        "message_id": message_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "thinking": thinking,
    }


async def get_messages_by_session(session_id: str) -> List[Dict]:
    """查询某会话的全部消息，按创建时间升序（对话顺序），thinking 反序列化为列表。"""
    conn = await get_connection()
    try:
        async with conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            messages: List[Dict] = []
            for row in rows:
                item = dict(row)
                item["thinking"] = (
                    json.loads(item["thinking"])
                    if item.get("thinking")
                    else None
                )
                messages.append(item)
            return messages
    finally:
        await conn.close()


async def clear_messages(session_id: str) -> None:
    """清空某会话的全部消息（新建会话或重置会话时调用）。"""
    conn = await get_connection()
    try:
        await conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        await conn.commit()
    finally:
        await conn.close()
