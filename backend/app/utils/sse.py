"""SSE 工具：封装 Server-Sent Events 消息格式，支持流式推送到前端。

事件名与前端 frontend/src/types/agent.ts 的 SSEEvent 类型严格对齐。
"""

import json
from typing import Any, Dict

# SSE 事件名约定（与前端 types/agent.ts 的 SSEEvent 严格对齐，禁止私自变更）
EVENT_START = "agent_start"
EVENT_NODE = "node"
EVENT_CONTENT = "content"
EVENT_TOOL_CALL = "tool_calls"
EVENT_DONE = "done"
EVENT_ERROR = "error"


def format_sse(event: str, data: Dict[str, Any]) -> str:
    """将事件格式化为 SSE 协议消息：event 行 + data 行（JSON）+ 空行结束。

    default=str 兜底非 JSON 序列化对象（如 datetime），保证推送不中断。
    """
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"
