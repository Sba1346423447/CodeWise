"""图节点测试补充：react_node / review_node / confirm_node / finalize_node。

LLM 与经验库全部打桩（不发真实请求）；confirm_node 的 interrupt 行为
通过 LangGraph 测试工具或直接验证挂起前逻辑。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.graph import nodes as graph_nodes
from app.core.graph.state import AgentState


def _assistant_message(tool_calls=None, content=""):
    """构造带工具调用的 assistant 消息。"""
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool_call(call_id, name, args):
    return {
        "id": call_id,
        "function": {"name": name, "arguments": json.dumps(args)},
    }


# ---------- react_node ----------


@pytest.fixture(autouse=True)
def _stub_experience(monkeypatch):
    """经验库检索打桩：返回空，避免连 ChromaDB。"""
    store = MagicMock()
    store.retrieve_similar.return_value = []
    store.add_experience.return_value = ""
    monkeypatch.setattr(graph_nodes, "experience_store", store)
    return store


async def test_react_node_llm_failure_degrades(monkeypatch):
    """LLM 调用失败：不中断，降级交付失败说明并重置审查状态。"""
    monkeypatch.setattr(
        graph_nodes.client, "chat_or_none", AsyncMock(return_value=None)
    )
    state = AgentState(task_desc="任务", messages=[{"role": "user", "content": "写快排"}])
    update = await graph_nodes.react_node(state)
    assert "模型调用失败" in update["final_message"]
    assert update["security_outcome"] == ""
    assert update["react_iterations"] == 1


def _mock_response(content="", tool_calls=None, finish_reason="stop"):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].finish_reason = finish_reason
    if tool_calls:
        tcs = []
        for name, args in tool_calls:
            tc = MagicMock()
            tc.function.name = name
            tc.function.arguments = args
            tcs.append(tc)
        resp.choices[0].message.tool_calls = tcs
    else:
        resp.choices[0].message.tool_calls = None
    return resp


async def test_react_node_answer_only(monkeypatch):
    """无代码无工具调用：判定通用问答，直接标记 is_answer_only。

    注意回答文本须为语法非法的 Python（中文标识符是合法表达式，会被
    extract_code 误提取），此处用含冒号的自然语言。
    """
    answer = "闭包是指：函数与其引用的外部变量共同构成的实体"
    monkeypatch.setattr(
        graph_nodes.client, "chat_or_none",
        AsyncMock(return_value=_mock_response(content=answer)),
    )
    state = AgentState(task_desc="解释闭包", messages=[])
    update = await graph_nodes.react_node(state)
    assert update["is_answer_only"] is True
    assert update["final_message"] == answer


async def test_react_node_extracts_code_from_content(monkeypatch):
    monkeypatch.setattr(
        graph_nodes.client, "chat_or_none",
        AsyncMock(return_value=_mock_response(content="```python\nprint('hi')\n```")),
    )
    update = await graph_nodes.react_node(AgentState(task_desc="任务", messages=[]))
    assert update["current_code"] == "print('hi')"


async def test_react_node_truncation_without_code(monkeypatch):
    """输出截断且无可解析内容：final_message 记录截断提示。

    源码既定行为（见注释）：截断后若内容本身可作问答回答（非空自然语言），
    answer_only 分支会覆盖截断提示、保留回答文本——问答优先于机械截断提示。
    """
    monkeypatch.setattr(
        graph_nodes.client, "chat_or_none",
        AsyncMock(return_value=_mock_response(content="截断的残缺输出：", finish_reason="length")),
    )
    update = await graph_nodes.react_node(AgentState(task_desc="任务", messages=[]))
    assert update["is_answer_only"] is True
    assert update["final_message"] == "截断的残缺输出："


# ---------- review_node ----------


async def test_review_node_no_tool_calls_blocks(monkeypatch):
    """无工具调用消息：异常兜底 block 回 react。"""
    state = AgentState(task_desc="任务", messages=[{"role": "user", "content": "hi"}])
    assert (await graph_nodes.review_node(state))["security_outcome"] == "block"


async def test_review_node_rule_block(monkeypatch):
    """第一层拦截级命中：全部拦截并回填 tool 消息。"""
    state = AgentState(
        task_desc="任务",
        messages=[_assistant_message(tool_calls=[
            _tool_call("c1", "code_executor", {"code": "import os\nos.system('rm -rf /')"})
        ])],
    )
    update = await graph_nodes.review_node(state)
    assert update["security_outcome"] == "block"
    assert update["messages"][0]["role"] == "tool"
    assert "安全审查拦截" in update["messages"][0]["content"]


async def test_review_node_safe_call_allows(monkeypatch):
    """普通安全调用（分类器打桩为 SAFE）：放行。"""
    monkeypatch.setattr(
        graph_nodes, "classify_tool_call",
        AsyncMock(return_value={"risk": "SAFE", "reason": ""}),
    )
    state = AgentState(
        task_desc="任务",
        messages=[_assistant_message(tool_calls=[
            _tool_call("c1", "linter", {"code": "print(1)"})
        ])],
    )
    assert (await graph_nodes.review_node(state))["security_outcome"] == "allow"


async def test_review_node_confirm_level(monkeypatch):
    """确认级命中（网络外联）：置 confirm 等人工确认。"""
    state = AgentState(
        task_desc="发请求",
        messages=[_assistant_message(tool_calls=[
            _tool_call("c1", "code_executor", {"code": "import requests\nrequests.get('http://x')"})
        ])],
    )
    update = await graph_nodes.review_node(state)
    assert update["security_outcome"] == "confirm"


# ---------- confirm_node 挂起前逻辑（无待确认项兜底） ----------


def test_confirm_node_no_pending_rejects():
    state = AgentState(task_desc="任务", security_decisions={})
    assert graph_nodes.confirm_node(state) == {"security_confirmation": False}


def test_confirm_node_no_pending_with_non_confirm_decisions():
    """decisions 存在但无 confirm 级条目：同样按拒绝兜底。"""
    state = AgentState(
        task_desc="任务",
        security_decisions={"c1": {"verdict": "SAFE", "reason": "", "tool": "linter"}},
    )
    assert graph_nodes.confirm_node(state) == {"security_confirmation": False}


# ---------- finalize_node ----------


@pytest.fixture()
def stub_summary(monkeypatch):
    """最终总结 LLM 打桩：降级为机械文案（返回 None）。"""
    monkeypatch.setattr(
        graph_nodes, "_build_final_summary", AsyncMock(return_value=None)
    )


async def test_finalize_answer_only(stub_summary):
    state = AgentState(task_desc="问答", is_answer_only=True, final_message="直接回答")
    update = await graph_nodes.finalize_node(state)
    assert update["final_code"] == "" and update["final_message"] == "直接回答"


async def test_finalize_success_delivery(stub_summary, _stub_experience):
    state = AgentState(
        task_desc="任务", current_code="print('v1')", tests_passed=True,
        test_results={"passed": 2}, reflection_count=1,
    )
    update = await graph_nodes.finalize_node(state)
    assert update["final_code"] == "print('v1')"
    assert update["tests_passed"] is True
    assert "已完成" in update["final_message"]
    _stub_experience.add_experience.assert_called_once()  # 通过即沉淀经验


async def test_finalize_fallback_to_best(stub_summary, _stub_experience):
    """当前版本改坏但历史最优通过过：回退交付 best_code。"""
    state = AgentState(
        task_desc="任务", current_code="print('bad')", tests_passed=False,
        best_code="print('best')", best_tests_passed=True, reflection_count=2,
    )
    update = await graph_nodes.finalize_node(state)
    assert update["final_code"] == "print('best')"
    assert update["tests_passed"] is True
    assert "回退" in update["final_message"]


async def test_finalize_env_error_treated_as_passed(stub_summary, _stub_experience):
    """编码类环境错误：用户视角按完成处理，测试面板隐藏。"""
    state = AgentState(
        task_desc="任务", current_code="print('v1')", tests_passed=False,
        test_results={"passed": 0, "failed": 1, "output": "UnicodeEncodeError: gbk"},
    )
    update = await graph_nodes.finalize_node(state)
    assert update["tests_passed"] is True
    assert update["test_results"] == {}


async def test_finalize_no_code_fallback_message(stub_summary):
    """无代码兜底：明确失败说明，绝不返回空白。"""
    state = AgentState(task_desc="任务")
    update = await graph_nodes.finalize_node(state)
    assert update["final_code"] == ""
    assert "未能生成" in update["final_message"]


# ---------- _is_env_error ----------


def test_is_env_error():
    assert graph_nodes._is_env_error({"output": "UnicodeEncodeError"}) is True
    assert graph_nodes._is_env_error({"output": "assert 1 == 2"}) is False
    assert graph_nodes._is_env_error(None) is False
