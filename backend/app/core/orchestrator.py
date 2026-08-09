"""编排器：协调 Graph / Tools / Memory / LLM 的调度中枢，管理完整 Agent 生命周期。

依赖：llm.config（模型名）、memory.conversation（对话记忆）、graph.builder（Agent 图）、
core.tools.*（内置工具集）。模块级单例 orchestrator 供 API 层与测试复用。
"""

import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..llm.config import config as llm_config
from ..memory.conversation import ConversationMemory
from ..utils.logger import get_logger
from .graph.builder import agent_graph
from .graph.state import AgentState
from .tools.code_executor import CodeExecutor
from .tools.linter import Linter
from .tools.registry import registry
from .tools.test_runner import TestRunner
from .tools.web_search import WebSearch

logger = get_logger("core.orchestrator")

# 异步事件回调签名：入参为节点事件字典
EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class AgentOrchestrator:
    """Agent 编排器：装配工具、管理会话记忆、驱动图执行并产出节点事件流。"""

    def __init__(self) -> None:
        self._register_default_tools()
        self.conversation = ConversationMemory()

    def _register_default_tools(self) -> None:
        """启动时一次性装配四个内置工具（幂等：已注册则跳过）。"""
        if not registry.list_names():
            registry.register_many(
                [
                    CodeExecutor(),
                    TestRunner(),
                    Linter(),
                    WebSearch(),
                ]
            )

    def reset_session(self) -> None:
        """清理会话级状态：对话历史（仅新建会话时调用）。

        反思记忆已随 AgentState 流转（每个图实例独立），不再需要全局清理。
        """
        self.conversation.clear()

    async def arun(
        self,
        task_desc: str,
        session_id: Optional[str] = None,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        on_event: Optional[EventCallback] = None,
    ) -> Dict[str, Any]:
        """异步执行完整 Agent 流程：逐节点推送事件，返回最终交付结果。

        多轮支持：
        - session_id：当前会话 ID；None 表示新建会话（前端首次请求不带）。
        - history_messages：该会话已持久化的历史对话（OpenAI messages 格式）。
          注入后作为上下文基础，实现"同会话内继续追问、代码演进记忆"。
        - on_event：可选异步回调，接收 {"type": "node", "node", "update"} 事件，
          供 API 层转为 SSE 流推送给前端。
        """
        logger.info("Agent 任务开始 | 会话={} 任务={}", session_id, task_desc[:100])
        start = time.monotonic()

        # 反思记忆已随 AgentState 流转（每次图执行新建 state，天然隔离），
        # 无需全局清理；对话历史由 history_messages 显式注入，实现跨轮记忆。
        # 组装上下文：历史消息 + 本轮用户消息。
        # 用局部 ConversationMemory 承载并做 max_messages 截断，避免长会话 token 爆炸。
        conv = ConversationMemory()
        for msg in history_messages or []:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and content:
                conv.add(role, content)
        conv.add("user", task_desc)

        initial_state = AgentState(
            task_desc=task_desc,
            messages=conv.get_messages(),
        )

        # 本轮行动摘要：按节点执行顺序收集，供前端折叠展示（贴近 Claude Code 叙事）
        thinking: List[Dict[str, Any]] = []

        final_state: Optional[AgentState] = None
        # 双流模式：updates 产出节点事件，values 产出最终完整状态
        async for mode, data in agent_graph.astream(
            initial_state, stream_mode=["updates", "values"]
        ):
            if mode == "updates":
                for node_name, update in data.items():
                    if on_event:
                        await on_event(
                            {"type": "node", "node": node_name, "update": update}
                        )
                    item = self._summarize_node(node_name, update)
                    if item:
                        thinking.append(item)
            else:
                final_state = data

        result = self._build_result(final_state)
        result["thinking"] = thinking
        # 消息元信息：耗时（毫秒）与模型名，供前端消息底部展示（WorkBuddy 风格）
        result["elapsed_ms"] = int((time.monotonic() - start) * 1000)
        result["model"] = llm_config.model
        logger.info(
            "Agent 任务结束 | 会话={} 耗时={:.2f}s tests_passed={} 代码长度={} 摘要步数={}",
            session_id,
            time.monotonic() - start,
            result["tests_passed"],
            len(result["final_code"]),
            len(thinking),
        )
        return result

    @staticmethod
    def _summarize_node(node_name: str, update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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
        if node_name == "tool_node":
            return {"type": "tool", "label": "执行工具", "detail": "读取工具执行结果"}
        if node_name == "test_gen_node":
            return {"type": "tool", "label": "生成验证", "detail": "为当前代码生成验证用例"}
        if node_name == "test_node":
            tr = update.get("test_results") or {}
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
    def _build_result(state: Optional[AgentState]) -> Dict[str, Any]:
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
        }


# 模块级单例：API 层与测试直接复用
orchestrator = AgentOrchestrator()
