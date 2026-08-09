"""Agent 对话接口：POST /api/agent，SSE 流式推送推理过程与最终结果。

依赖：fastapi（路由 / StreamingResponse / SSE）、core.orchestrator（编排器）、
models.*（会话 / 步骤 / 消息落库）、utils.sse（SSE 消息格式化）。
"""

import asyncio
import json
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..core.orchestrator import orchestrator
from ..models import message as message_model
from ..models import session as session_model
from ..models import step as step_model
from ..utils.logger import get_logger
from ..utils.sse import (
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_NODE,
    EVENT_START,
    format_sse,
)

logger = get_logger("api.agent")

router = APIRouter(prefix="/api", tags=["agent"])

# 图节点名 → 步骤类型映射
_NODE_TO_STEP = {
    "react_node": step_model.STEP_REACT,
    "tool_node": step_model.STEP_TOOL,
    "test_gen_node": step_model.STEP_TOOL,  # 测试生成节点，归入工具执行类
    "test_node": step_model.STEP_TOOL,  # 强制测试节点，归入工具执行类
    "reflect_node": step_model.STEP_REFLECT,
    "refine_node": step_model.STEP_REFINE,
    "finalize_node": step_model.STEP_REFINE,  # 最终交付，归入优化完成类
}


class AgentRequest(BaseModel):
    """Agent 任务请求体（多轮对话：session_id 可选，复用会话时必带）。"""

    task_desc: str
    session_id: Optional[str] = None


def _build_assistant_content(result: dict) -> str:
    """将 Agent 交付结果组装为助手消息的 Markdown 正文（总结 + 最终代码）。

    与前端实时渲染逻辑保持一致，历史会话回放直接渲染此 content 即可对齐。
    """
    parts = [result.get("final_message") or ""]
    final_code = result.get("final_code") or ""
    if final_code.strip():
        parts.append(f"\n\n```python\n{final_code}\n```")
    return "\n".join(p for p in parts if p).strip()


@router.post("/agent")
async def run_agent(request: AgentRequest) -> StreamingResponse:
    """Agent 对话接口：SSE 流式推送节点事件与最终结果，对话与执行轨迹落库。

    多轮：复用 session_id 时从库加载历史消息作为上下文注入编排器，实现代码演进记忆；
    无 session_id 视为新建会话，后端创建新会话并返回 session_id。
    """
    task_desc = request.task_desc.strip()
    if not task_desc:
        raise HTTPException(status_code=400, detail="task_desc 不能为空")

    # 会话解析：有 session_id 复用（校验存在并加载历史），否则新建
    if request.session_id:
        existing = await session_model.get_session(request.session_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        session_id = request.session_id
        stored = await message_model.get_messages_by_session(session_id)
        history_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in stored
            if m["role"] in ("user", "assistant")
        ]
    else:
        created = await session_model.create_session(task_desc)
        session_id = created["session_id"]
        history_messages = []

    # 落库本轮用户消息（供会话回放与多轮上下文加载）
    await message_model.add_message(session_id, message_model.ROLE_USER, task_desc)
    logger.info("收到 Agent 请求 | session_id={} 任务={} 历史消息数={}",
                session_id, task_desc[:100], len(history_messages))

    async def event_stream() -> AsyncGenerator[str, None]:
        # 队列桥接：编排器回调协程放消息，SSE 生成器消费并 yield
        queue: "asyncio.Queue[object]" = asyncio.Queue()
        sentinel = object()

        async def on_node(event: dict) -> None:
            """节点事件回调：记录执行步骤并推送 SSE 节点事件。"""
            node = event.get("node", "")
            logger.info("推送节点事件 | session_id={} node={}", session_id, node)
            await step_model.create_step(
                session_id=session_id,
                step_type=_NODE_TO_STEP.get(node, step_model.STEP_REACT),
                output_data=json.dumps(event.get("update", {}), ensure_ascii=False)[:2000],
            )
            await queue.put(format_sse(EVENT_NODE, event))

        async def run_and_finish() -> None:
            """后台执行完整 Agent 流程，结束或异常时推送终态事件并落库对话消息。"""
            try:
                result = await orchestrator.arun(
                    task_desc,
                    session_id=session_id,
                    history_messages=history_messages,
                    on_event=on_node,
                )
                await session_model.update_session(
                    session_id,
                    status=session_model.STATUS_COMPLETED,
                    final_code=result["final_code"],
                )
                # 落库助手交付消息（总结 + 代码），供历史回放。
                # thinking 打包结构化字段，前端回放时拆出复用实时结构化组件渲染，
                # 保证历史对话与实时对话展示效果一致（见 App.tsx handleSelect）
                await message_model.add_message(
                    session_id,
                    message_model.ROLE_ASSISTANT,
                    _build_assistant_content(result),
                    thinking={
                        "steps": result.get("thinking") or [],
                        "code": result.get("final_code") or "",
                        "tests_passed": result.get("tests_passed"),
                        "reflections": result.get("reflection_count"),
                        "test_results": result.get("test_results") or {},
                        "model": result.get("model"),
                        "elapsed_ms": result.get("elapsed_ms"),
                    },
                )
                await queue.put(format_sse(EVENT_DONE, result))
            except Exception as exc:
                logger.exception("Agent 执行异常 | session_id={} 错误={}", session_id, exc)
                await session_model.update_session(
                    session_id, status=session_model.STATUS_FAILED
                )
                await queue.put(format_sse(EVENT_ERROR, {"message": str(exc)}))
            finally:
                await queue.put(sentinel)

        yield format_sse(EVENT_START, {"session_id": session_id})
        runner = asyncio.create_task(run_and_finish())

        while True:
            msg = await queue.get()
            if msg is sentinel:
                break
            yield msg

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁止 nginx 缓冲，保证 SSE 实时推送
        },
    )
