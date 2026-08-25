"""编排器测试：节点摘要提炼 / 结果组装 / arun-aresume 生命周期（mock 图执行）。

agent_graph.astream 用假异步生成器替换（不发真实 LLM 请求），
compress_messages 同样打桩，验证编排层自身的分流与组装逻辑。
"""

from unittest.mock import AsyncMock

import pytest

from app.core.graph.state import AgentState
from app.core.orchestrator import AgentOrchestrator
from app.llm.config import config as llm_config


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """压缩与图执行全部打桩：编排器测试不触 LLM。"""

    async def fake_compress(messages, model=""):
        return messages

    monkeypatch.setattr("app.core.orchestrator.compress_messages", fake_compress)


# ---------- _summarize_node：各节点类型的摘要提炼 ----------


def test_summarize_react_with_tool_calls():
    update = {"messages": [{"tool_calls": [{"function": {"name": "linter"}}]}]}
    item = AgentOrchestrator._summarize_node("react_node", update)
    assert item["type"] == "tool" and "linter" in item["detail"]


def test_summarize_react_analysis():
    update = {"messages": [{"content": "分析需求中"}]}
    item = AgentOrchestrator._summarize_node("react_node", update)
    assert item["label"] == "分析需求"


@pytest.mark.parametrize("outcome,label", [
    ("allow", "安全审查通过"), ("block", "安全审查拦截"), ("confirm", "安全审查待确认"),
])
def test_summarize_review_outcomes(outcome, label):
    assert label in AgentOrchestrator._summarize_node(
        "review_node", {"security_outcome": outcome}
    )["detail"]


def test_summarize_confirmation_results():
    assert "批准" in AgentOrchestrator._summarize_node(
        "confirm_node", {"security_confirmation": True}
    )["detail"]
    assert "拒绝" in AgentOrchestrator._summarize_node(
        "confirm_node", {"security_confirmation": False}
    )["detail"]


def test_summarize_test_node_stats():
    item = AgentOrchestrator._summarize_node(
        "test_node", {"test_results": {"passed": 2, "failed": 1, "errors": 0, "output": ""}}
    )
    assert "通过 2" in item["detail"] and "失败 1" in item["detail"]


def test_summarize_test_node_env_error_hidden():
    """环境类错误（编码问题）不产出摘要：环境细节不面向用户。"""
    assert AgentOrchestrator._summarize_node(
        "test_node", {"test_results": {"passed": 0, "failed": 1, "output": "UnicodeEncodeError"}}
    ) is None


def test_summarize_refine_with_diff():
    item = AgentOrchestrator._summarize_node(
        "refine_node",
        {"reflections": [{"previous_code": "a", "refined_code": "b"}]},
    )
    assert item["diff"] == {"before": "a", "after": "b"}


def test_summarize_unknown_node_returns_none():
    assert AgentOrchestrator._summarize_node("mystery_node", {}) is None


# ---------- _build_result：状态组装 ----------


def test_build_result_none_state():
    result = AgentOrchestrator._build_result(None)
    assert result["final_code"] == "" and result["tests_passed"] is False


def test_build_result_dict_state():
    state = {
        "task_desc": "任务", "final_code": "print(1)", "final_message": "完成",
        "messages": [], "reflection_count": 1, "tests_passed": True,
        "test_results": {"passed": 1}, "model": "",
    }
    result = AgentOrchestrator._build_result(state)
    assert result["final_code"] == "print(1)"
    assert result["model"] == llm_config.model  # 空 model 回退全局默认


def test_build_result_end_wrapped_and_fallback_code():
    """__end__ 包裹剥层 + final_code 缺失时回落 current_code。"""
    result = AgentOrchestrator._build_result({"__end__": {"current_code": "x", "model": ""}})
    assert result["final_code"] == "x"


def test_build_result_pydantic_state():
    state = AgentState(task_desc="任务", messages=[], model="m1")
    state.current_code = "print(2)"
    result = AgentOrchestrator._build_result(state)
    assert result["final_code"] == "print(2)" and result["model"] == "m1"


# ---------- arun / aresume 生命周期（mock _stream_graph） ----------


def _final_state_dict() -> dict:
    return {
        "task_desc": "任务", "final_code": "print('ok')", "final_message": "完成",
        "messages": [], "reflection_count": 1, "tests_passed": True,
        "test_results": {"passed": 3}, "model": "test-model",
    }


async def test_arun_normal_delivery(monkeypatch):
    thinking = [{"type": "react", "label": "分析需求", "detail": "..."}]
    monkeypatch.setattr(
        AgentOrchestrator, "_stream_graph",
        AsyncMock(return_value=(_final_state_dict(), thinking, None)),
    )
    events = []
    result = await AgentOrchestrator().arun(
        "写快排", history_messages=[{"role": "user", "content": "历史"}],
        on_event=lambda e: events.append(e) or _noop(),
    )
    assert result["final_code"] == "print('ok')"
    assert result["thinking"] == thinking
    assert result["pending_confirmation"] is None
    assert result["elapsed_ms"] >= 0


async def _noop():
    return None


async def test_arun_pending_confirmation(monkeypatch):
    pending = {"run_id": "r1", "tools": [{"name": "web_search"}]}
    monkeypatch.setattr(
        AgentOrchestrator, "_stream_graph",
        AsyncMock(return_value=(None, [], pending)),
    )
    result = await AgentOrchestrator().arun("任务")
    assert result["pending_confirmation"] == pending
    assert "人工确认" in result["final_message"]
    assert result["tests_passed"] is False  # 挂起轮无交付物


async def test_arun_filters_invalid_history(monkeypatch):
    """历史消息中仅 user/assistant 且有正文者进入上下文。"""
    captured = {}

    async def fake_compress(messages, model=""):
        captured["messages"] = messages
        return messages

    monkeypatch.setattr("app.core.orchestrator.compress_messages", fake_compress)
    monkeypatch.setattr(
        AgentOrchestrator, "_stream_graph",
        AsyncMock(return_value=(_final_state_dict(), [], None)),
    )
    await AgentOrchestrator().arun(
        "新任务",
        history_messages=[
            {"role": "user", "content": "有效"},
            {"role": "system", "content": "无效角色"},
            {"role": "assistant", "content": ""},
        ],
    )
    assert captured["messages"] == [
        {"role": "user", "content": "有效"},
        {"role": "user", "content": "新任务"},
    ]


async def test_aresume_normal_and_pending(monkeypatch):
    monkeypatch.setattr(
        AgentOrchestrator, "_stream_graph",
        AsyncMock(return_value=(_final_state_dict(), [], None)),
    )
    result = await AgentOrchestrator().aresume("r1", True)
    assert result["pending_confirmation"] is None and result["final_code"] == "print('ok')"

    pending = {"run_id": "r1", "tools": []}
    monkeypatch.setattr(
        AgentOrchestrator, "_stream_graph",
        AsyncMock(return_value=(None, [], pending)),
    )
    again = await AgentOrchestrator().aresume("r1", False)
    assert again["pending_confirmation"] == pending
    assert "还有操作需要人工确认" in again["final_message"]


def test_register_default_tools_idempotent():
    """重复构造编排器不重复注册（幂等）。"""
    AgentOrchestrator()
    AgentOrchestrator()
    from app.core.tools.registry import registry
    names = registry.list_names()
    assert names.count("linter") == 1
