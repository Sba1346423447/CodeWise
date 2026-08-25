"""编排器：协调 Graph / Tools / Memory / LLM 的调度中枢，管理完整 Agent 生命周期。

依赖：llm.config（模型名）、memory.conversation（上下文压缩）、graph.builder（Agent 图）、
core.tools.*（内置工具集）。模块级单例 orchestrator 供 API 层与测试复用。

human-in-the-loop：confirm_node 的 interrupt 挂起由本层检测并经 on_event 推送
confirmation_required 事件；用户响应后由 aresume 以 Command(resume=...) 恢复
同一线程（thread_id = run_id，一次请求对应一个图执行线程）。
"""

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.types import Command

from ..llm.config import config as llm_config
from ..memory.conversation import compress_messages
from ..utils.logger import get_logger
from .graph.builder import agent_graph
from .graph.state import AgentState
from .tools.code_executor import CodeExecutor
from .tools.file_editor import FileEditor
from .tools.linter import Linter
from .tools.registry import registry
from .tools.test_runner import TestRunner
from .tools.web_search import WebSearch

logger = get_logger("core.orchestrator")

# 异步事件回调签名：入参为节点事件字典
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class AgentOrchestrator:
    """Agent 编排器：装配工具、驱动图执行并产出节点事件流。

    多轮会话记忆不在此维护：历史消息由 API 层从数据库加载，
    经 arun 的 history_messages 参数注入后由 compress_messages 压缩。
    """

    def __init__(self) -> None:
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """启动时一次性装配内置工具（幂等：已注册则跳过）。"""
        if not registry.list_names():
            registry.register_many(
                [
                    CodeExecutor(),
                    TestRunner(),
                    Linter(),
                    WebSearch(),
                    FileEditor(),
                ]
            )

    async def arun(
        self,
        task_desc: str,
        session_id: str | None = None,
        history_messages: list[dict[str, Any]] | None = None,
        on_event: EventCallback | None = None,
        model: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """异步执行完整 Agent 流程：逐节点推送事件，返回最终交付结果。

        多轮支持：
        - session_id：当前会话 ID；None 表示新建会话（前端首次请求不带）。
        - history_messages：该会话已持久化的历史对话（OpenAI messages 格式）。
          注入后作为上下文基础，实现"同会话内继续追问、代码演进记忆"。
        - on_event：可选异步回调，接收 {"type": "node", "node", "update"} 与
          {"type": "confirmation_required", ...} 事件，供 API 层转为 SSE 推送。
        - model：本次请求选择的模型名（可选）；为空时使用后端默认配置。
        - run_id：本次图执行线程 ID（checkpointer thread_id）；为空自动生成。
          中途 interrupt 挂起后，前端凭 confirmation_required 事件中的 run_id
          发起确认请求，由 aresume 恢复同一线程。
        """
        logger.info("Agent 任务开始 | 会话={} 任务={}", session_id, task_desc[:100])
        start = time.monotonic()
        thread_id = run_id or uuid.uuid4().hex

        # 组装上下文：历史消息 + 本轮用户消息。
        # 长会话用滚动摘要压缩（compress_messages）替代机械截断，
        # 保留早期语义（需求演进 / 已定决策），避免多轮对话丢信息。
        context: list[dict[str, str]] = []
        for msg in history_messages or []:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and content:
                context.append({"role": role, "content": content})
        context.append({"role": "user", "content": task_desc})
        context = await compress_messages(context, model=model)

        initial_state = AgentState(
            task_desc=task_desc,
            messages=context,
            model=model or "",
        )

        final_state, thinking, pending = await self._stream_graph(
            initial_state, thread_id, on_event
        )

        if pending:
            # 安全审查挂起：图在 confirm_node interrupt 处暂停，本轮不产出交付物。
            # 构造挂起结果（pending_confirmation 供前端弹确认框），会话状态
            # 由 API 层置为 awaiting_confirmation，恢复后继续跑完。
            result = {
                "task_desc": task_desc,
                "final_code": "",
                "final_message": "安全审查检测到需要人工确认的操作，已推送确认请求。",
                "messages": [],
                "reflection_count": 0,
                "tests_passed": False,
                "test_results": {},
                "model": model or llm_config.model,
                "thinking": thinking,
                "pending_confirmation": pending,
            }
            result["elapsed_ms"] = int((time.monotonic() - start) * 1000)
            logger.info("Agent 任务挂起等待人工确认 | 会话={} run_id={}", session_id, thread_id)
            return result

        result = self._build_result(final_state)
        result["thinking"] = thinking
        result["pending_confirmation"] = None
        # 消息元信息：耗时（毫秒），供前端消息底部展示（WorkBuddy 风格）；
        # model 已由 _build_result 从 state 提取（用户选择的模型），不再取全局默认
        result["elapsed_ms"] = int((time.monotonic() - start) * 1000)
        logger.info(
            "Agent 任务结束 | 会话={} 耗时={:.2f}s tests_passed={} 代码长度={} 摘要步数={}",
            session_id,
            time.monotonic() - start,
            result["tests_passed"],
            len(result["final_code"]),
            len(thinking),
        )
        return result

    async def aresume(
        self,
        run_id: str,
        approved: bool,
        on_event: EventCallback | None = None,
    ) -> dict[str, Any]:
        """恢复挂起的人工确认线程：用户批准/拒绝后继续执行图。

        原理：confirm_node 在 interrupt 处重跑，interrupt() 直接返回 resume 值
        （True 批准 / False 拒绝），图从断点继续走完（可能再次 interrupt——
        同一线程多轮确认，此时返回新的挂起结果，前端继续弹框）。
        """
        logger.info("恢复人工确认线程 | run_id={} 批准={}", run_id, approved)
        start = time.monotonic()

        final_state, thinking, pending = await self._stream_graph(
            Command(resume=approved), run_id, on_event
        )

        if pending:
            result = {
                "task_desc": "",
                "final_code": "",
                "final_message": "还有操作需要人工确认，已再次推送确认请求。",
                "messages": [],
                "reflection_count": 0,
                "tests_passed": False,
                "test_results": {},
                "model": llm_config.model,
                "thinking": thinking,
                "pending_confirmation": pending,
            }
            result["elapsed_ms"] = int((time.monotonic() - start) * 1000)
            return result

        result = self._build_result(final_state)
        result["thinking"] = thinking
        result["pending_confirmation"] = None
        result["elapsed_ms"] = int((time.monotonic() - start) * 1000)
        logger.info("人工确认线程恢复执行完成 | run_id={} tests_passed={}", run_id, result["tests_passed"])
        return result

    async def _stream_graph(
        self,
        graph_input: Any,
        thread_id: str,
        on_event: EventCallback | None,
    ) -> tuple[AgentState | None, list[dict[str, Any]], dict[str, Any] | None]:
        """驱动图执行并消费双流（arun / aresume 共用）。

        - updates 流：逐节点事件经 on_event 推送 + 提炼行动摘要（thinking）；
          其中 __interrupt__ 条目表示 confirm_node 挂起，提炼为确认请求事件
        - values 流：捕获最终完整状态
        返回 (final_state, thinking, pending_confirmation)。
        """
        thinking: list[dict[str, Any]] = []
        final_state: AgentState | None = None
        pending_confirmation: dict[str, Any] | None = None
        config = {"configurable": {"thread_id": thread_id}}

        async for mode, data in agent_graph.astream(
            graph_input, config, stream_mode=["updates", "values"]
        ):
            if mode == "updates":
                for node_name, update in data.items():
                    if node_name == "__interrupt__":
                        # confirm_node 挂起：提炼待确认信息，推送确认请求事件
                        interrupts = update if isinstance(update, tuple) else (update,)
                        for intr in interrupts:
                            value = getattr(intr, "value", None) or {}
                            tools = value.get("pending_tools") or []
                            if not tools:
                                continue
                            pending_confirmation = {"run_id": thread_id, "tools": tools}
                            if on_event:
                                await on_event(
                                    {"type": "confirmation_required", **pending_confirmation}
                                )
                        continue
                    if on_event:
                        await on_event(
                            {"type": "node", "node": node_name, "update": update}
                        )
                    item = self._summarize_node(node_name, update)
                    if item:
                        thinking.append(item)
            else:
                final_state = data

        return final_state, thinking, pending_confirmation

    @staticmethod
    def _summarize_node(node_name: str, update: dict[str, Any]) -> dict[str, Any] | None:
        """从单个节点 update 提炼一条"行动摘要"，供前端折叠展示；无有效信息返回 None。

        提炼原则：只保留用户可理解的行动与结论，不暴露原始 JSON 细节。
        """
        if node_name == "react_node":
            # 本轮 LLM 决策：产出代码或调用工具
            messages = update.get("messages") or []
            last = messages[-1] if messages else {}
            tool_calls = last.get("tool_calls") or []
            if tool_calls:
                names = [
                    tc.get("function", {}).get("name", "工具")
                    for tc in tool_calls
                    if isinstance(tc, dict)
                ]
                return {"type": "tool", "label": "调用工具", "detail": "、".join(names)}
            return {"type": "react", "label": "分析需求", "detail": (last.get("content") or "")[:80]}
        if node_name == "review_node":
            # 安全审查：规则过滤 + AI 风险分类的三级结论
            outcome = update.get("security_outcome") or ""
            label = {
                "allow": "安全审查通过",
                "block": "安全审查拦截",
                "confirm": "安全审查待确认",
            }.get(outcome, "安全审查")
            return {"type": "tool", "label": "安全审查", "detail": label}
        if node_name == "confirm_node":
            # 人工确认结果（恢复轮产出；挂起轮只发 confirmation_required 事件）
            confirmed = update.get("security_confirmation")
            detail = "用户批准执行" if confirmed else "用户拒绝执行"
            return {"type": "tool", "label": "人工确认", "detail": detail}
        if node_name == "code_review_node":
            # 代码安全审查：代码主链路进入测试执行前的三级结论
            outcome = update.get("security_outcome") or ""
            label = {
                "allow": "代码安全审查通过",
                "block": "代码安全审查拦截",
                "confirm": "代码安全审查待确认",
            }.get(outcome, "代码安全审查")
            return {"type": "tool", "label": "代码安全审查", "detail": label}
        if node_name == "code_confirm_node":
            # 代码人工确认结果（恢复轮产出；挂起轮只发 confirmation_required 事件）
            confirmed = update.get("security_confirmation")
            detail = "用户批准执行代码" if confirmed else "用户拒绝执行代码"
            return {"type": "tool", "label": "代码确认", "detail": detail}
        if node_name == "tool_node":
            return {"type": "tool", "label": "执行工具", "detail": "读取工具执行结果"}
        if node_name == "test_gen_node":
            return {"type": "tool", "label": "生成验证", "detail": "为当前代码生成验证用例"}
        if node_name == "test_node":
            tr = update.get("test_results") or {}
            # 环境因素导致的测试失败（编码类错误）不向用户展示统计数字——
            # 前端只面向结果，环境细节仅进后端日志（见 finalize_node 的 env_error 处理）
            output = (tr.get("output") or "")
            if any(marker in output for marker in ("UnicodeEncodeError", "UnicodeDecodeError", "LookupError")):
                return None
            passed = tr.get("passed", 0)
            failed = tr.get("failed", 0)
            errors = tr.get("errors", 0)
            detail = f"通过 {passed} · 失败 {failed} · 错误 {errors}"
            return {"type": "reflect", "label": "运行验证", "detail": detail}
        if node_name == "reflect_node":
            critique = update.get("critique") or ""
            return {"type": "reflect", "label": "审查代码", "detail": critique[:80]}
        if node_name == "refine_node":
            # 从本轮反思记录中取前后代码，供前端 Diff 视图展示"本轮改了什么"
            records = update.get("reflections") or []
            record = records[-1] if records else {}
            previous = (record.get("previous_code") or "").strip()
            refined = (record.get("refined_code") or "").strip()
            item = {"type": "refine", "label": "优化代码", "detail": "按审查意见重写实现"}
            if previous and refined and previous != refined:
                item["diff"] = {"before": previous, "after": refined}
            return item
        if node_name == "finalize_node":
            return {"type": "refine", "label": "交付结果", "detail": (update.get("final_message") or "")[:80]}
        return None

    @staticmethod
    def _build_result(state: AgentState | None) -> dict[str, Any]:
        """从最终状态组装对外交付结果（final_code 兜底取 current_code）。

        兼容 Pydantic AgentState 实例（dict 形式）：LangGraph 1.x 的 values 模式
        yield 的是 dict 而非 Pydantic 对象，需用 .get() 兜底。
        """
        empty = {
            "task_desc": "",
            "final_code": "",
            "final_message": "",
            "messages": [],
            "reflection_count": 0,
            "tests_passed": False,
            "test_results": {},
            "elapsed_ms": 0,
            "model": llm_config.model,
            "thinking": [],
        }
        if state is None:
            return empty

        # LangGraph 1.x values 模式有时 yield {'__end__': state_dict} 包裹，剥一层
        if isinstance(state, dict):
            if "__end__" in state:
                state = state["__end__"]
            return {
                "task_desc": state.get("task_desc", ""),
                "final_code": state.get("final_code") or state.get("current_code") or "",
                "final_message": state.get("final_message", ""),
                "messages": state.get("messages", []),
                "reflection_count": state.get("reflection_count", 0),
                "tests_passed": state.get("tests_passed", False),
                "test_results": state.get("test_results", {}),
                # 实际使用的模型：优先取用户本轮选择的 model（state 流转），
                # 空串/缺失表示未指定，回退全局默认配置（与 client._build_kwargs 语义一致）
                "model": state.get("model") or llm_config.model,
            }

        # 兜底：Pydantic 实例走属性访问
        return {
            "task_desc": state.task_desc,
            "final_code": state.final_code or state.current_code,
            "final_message": state.final_message,
            "messages": state.messages,
            "reflection_count": state.reflection_count,
            "tests_passed": state.tests_passed,
            "test_results": state.test_results,
            "model": state.model or llm_config.model,
        }


# 模块级单例：API 层与测试直接复用
orchestrator = AgentOrchestrator()
