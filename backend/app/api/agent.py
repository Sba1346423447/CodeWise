"""Agent 对话接口：POST /api/agent，SSE 流式推送推理过程与最终结果。

依赖：fastapi（路由 / StreamingResponse / SSE）、core.orchestrator（编排器）、
models.*（会话 / 步骤 / 消息落库）、utils.sse（SSE 消息格式化）。

human-in-the-loop：安全审查判 confirm 时图在 confirm_node 挂起，SSE 推送
confirmation_required 事件（携带 run_id 与待确认操作）；前端弹确认框后以
confirmation 字段（run_id + approved）重新请求本接口，恢复挂起线程继续执行。

停止生成：run_and_finish 以独立 asyncio Task 运行（SSE 断开不影响其执行），
按 run_id 注册到 _RUNNING_TASKS；前端"停止"按钮先调 POST /api/agent/stop
取消该 Task（真正终止图执行与 LLM 调用），再断开本地 SSE 连接。
"""

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..core.orchestrator import orchestrator
from ..models import message as message_model
from ..models import session as session_model
from ..models import step as step_model
from ..utils.logger import get_logger
from ..utils.sse import (
    EVENT_CONFIRMATION,
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_NODE,
    EVENT_START,
    format_sse,
)

logger = get_logger("api.agent")

router = APIRouter(prefix="/api", tags=["agent"])

# 运行中任务注册表：run_id → asyncio.Task（run_and_finish）。
# 前端停止生成时凭 run_id 找到任务并 cancel，真正终止图执行；
# 任务结束（正常/异常/取消）后自行注销，注册表始终只含活跃任务。
_RUNNING_TASKS: dict[str, "asyncio.Task[None]"] = {}

# 图节点名 → 步骤类型映射
_NODE_TO_STEP = {
    "react_node": step_model.STEP_REACT,
    "review_node": step_model.STEP_TOOL,  # 安全审查节点，归入工具执行类
    "confirm_node": step_model.STEP_TOOL,  # 人工确认节点，归入工具执行类
    "code_review_node": step_model.STEP_TOOL,  # 代码安全审查节点，归入工具执行类
    "code_confirm_node": step_model.STEP_TOOL,  # 代码人工确认节点，归入工具执行类
    "tool_node": step_model.STEP_TOOL,
    "test_gen_node": step_model.STEP_TOOL,  # 测试生成节点，归入工具执行类
    "test_node": step_model.STEP_TOOL,  # 强制测试节点，归入工具执行类
    "reflect_node": step_model.STEP_REFLECT,
    "refine_node": step_model.STEP_REFLECT,
    "finalize_node": step_model.STEP_REFINE,  # 最终交付，归入优化完成类
}


class ConfirmationRequest(BaseModel):
    """人工确认请求体：恢复挂起的图线程（run_id 来自 confirmation_required 事件）。"""

    run_id: str
    approved: bool


class AgentRequest(BaseModel):
    """Agent 任务请求体（多轮对话：session_id 可选，复用会话时必带）。

    confirmation 非空时为人工确认恢复模式：不新建用户消息，恢复 run_id 对应的
    挂起线程（此时 task_desc 可为空）。
    """

    task_desc: str | None = None
    session_id: str | None = None
    model: str | None = None  # 前端模型下拉选择；为空时后端使用默认配置
    confirmation: ConfirmationRequest | None = None


class StopRequest(BaseModel):
    """停止生成请求体：run_id 来自 agent_start 事件（本轮图执行线程 ID）。"""

    run_id: str


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

    人工确认：request.confirmation 非空时为恢复模式——恢复 run_id 对应的挂起
    图线程（用户批准/拒绝安全审查待确认的操作），不新建用户消息。
    """
    # ---------- 人工确认恢复模式 ----------
    if request.confirmation is not None:
        if not request.session_id:
            raise HTTPException(status_code=400, detail="确认请求必须携带 session_id")
        existing = await session_model.get_session(request.session_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        session_id = request.session_id
        run_id = request.confirmation.run_id
        approved = request.confirmation.approved
        history_messages: list = []
        task_desc = ""
        logger.info("收到人工确认请求 | session_id={} run_id={} 批准={}",
                    session_id, run_id, approved)
    else:
        # ---------- 正常任务模式 ----------
        task_desc = (request.task_desc or "").strip()
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
        # 本轮图执行线程 ID：confirm_node 挂起后前端凭此恢复
        run_id = uuid.uuid4().hex
        approved = False

    async def event_stream() -> AsyncGenerator[str, None]:
        # 队列桥接：编排器回调协程放消息，SSE 生成器消费并 yield
        queue: asyncio.Queue[object] = asyncio.Queue()
        sentinel = object()

        async def on_event(event: dict) -> None:
            """编排器事件回调：节点事件落库并推送；确认请求事件直推 SSE。"""
            if event.get("type") == "confirmation_required":
                logger.info("推送确认请求事件 | session_id={} run_id={}",
                            session_id, event.get("run_id"))
                await queue.put(format_sse(EVENT_CONFIRMATION, event))
                return
            node = event.get("node", "")
            logger.info("推送节点事件 | session_id={} node={}", session_id, node)
            await step_model.create_step(
                session_id=session_id,
                step_type=_NODE_TO_STEP.get(node, step_model.STEP_REACT),
                output_data=json.dumps(event.get("update", {}), ensure_ascii=False)[:2000],
            )
            await queue.put(format_sse(EVENT_NODE, event))

        async def run_and_finish() -> None:
            """后台执行完整 Agent 流程，结束或异常时推送终态事件并落库对话消息。

            取消处理：POST /api/agent/stop 会对本 Task 调 cancel()，CancelledError
            在最近的 await 点注入（图执行与 LLM 调用同步终止）。此处消费该异常并
            完成收尾（会话置 stopped + 落库中断说明），保证停止后状态一致可回放。
            """
            try:
                if request.confirmation is not None:
                    # 恢复模式：从 confirm_node 断点继续（批准/拒绝）
                    result = await orchestrator.aresume(
                        run_id, approved, on_event=on_event
                    )
                else:
                    result = await orchestrator.arun(
                        task_desc,
                        session_id=session_id,
                        history_messages=history_messages,
                        on_event=on_event,
                        model=request.model,
                        run_id=run_id,
                    )
                pending = result.get("pending_confirmation")
                if pending:
                    # 挂起：不落助手消息（尚未交付），会话进入等待确认状态；
                    # 恢复轮的最终结果仍会正常落库
                    await session_model.update_session(
                        session_id, status=session_model.STATUS_AWAITING
                    )
                    await queue.put(format_sse(EVENT_DONE, result))
                    return
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
            except asyncio.CancelledError:
                # 用户停止生成：cancel() 在 orchestrator 的 await 点注入异常，
                # 图执行与 LLM 调用已终止。收尾落库（stopped 状态 + 中断说明），
                # 供会话历史回放；事件推送尽力而为（前端通常已断开 SSE）。
                logger.info("Agent 任务被用户停止 | session_id={} run_id={}", session_id, run_id)
                try:
                    await session_model.update_session(
                        session_id, status=session_model.STATUS_STOPPED
                    )
                    await message_model.add_message(
                        session_id,
                        message_model.ROLE_ASSISTANT,
                        "⏹ 已停止：本次任务被手动中断，未产出最终结果。",
                    )
                    await queue.put(format_sse(EVENT_DONE, {
                        "final_code": "",
                        "final_message": "⏹ 已停止：本次任务被手动中断。",
                        "stopped": True,
                    }))
                except Exception:
                    logger.exception("停止收尾落库失败 | session_id={}", session_id)
                # 消费掉取消信号（不 re-raise）：任务以正常结束收尾，
                # finally 的 sentinel 仍会执行，SSE 消费循环得以退出
            except Exception as exc:
                logger.exception("Agent 执行异常 | session_id={} 错误={}", session_id, exc)
                await session_model.update_session(
                    session_id, status=session_model.STATUS_FAILED
                )
                await queue.put(format_sse(EVENT_ERROR, {"message": str(exc)}))
            finally:
                _RUNNING_TASKS.pop(run_id, None)
                await queue.put(sentinel)

        # agent_start 携带 run_id：前端确认时回传，恢复同一线程
        yield format_sse(EVENT_START, {"session_id": session_id, "run_id": run_id})
        runner = asyncio.create_task(run_and_finish())
        # 注册运行任务：停止端点凭 run_id 定位并 cancel（见 POST /api/agent/stop）
        _RUNNING_TASKS[run_id] = runner

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


@router.post("/agent/stop")
async def stop_agent(request: StopRequest) -> dict:
    """停止生成：取消 run_id 对应的运行中任务，真正终止图执行与 LLM 调用。

    幂等设计：任务不存在（已结束 / 挂起等待确认 / 从未运行）返回 stopped=False，
    不报错——前端停止按钮与后端任务生命周期存在竞态，重复点击不应失败。
    取消后的收尾（会话置 stopped + 中断说明落库）由任务自身的
    CancelledError 分支完成（见 run_and_finish）。
    """
    task = _RUNNING_TASKS.get(request.run_id)
    if task is None or task.done():
        logger.info("停止请求无运行中任务 | run_id={}", request.run_id)
        return {"stopped": False}
    task.cancel()
    logger.info("已请求取消 Agent 任务 | run_id={}", request.run_id)
    return {"stopped": True}
