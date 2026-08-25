"""Agent 接口 SSE 全链路测试：mock 编排器，验证事件流/落库/停止幂等路径。

不发起真实 LLM 请求：monkeypatch orchestrator.arun 返回固定结果，
走真实 StreamingResponse 队列桥接与数据库读写。
任务取消细节（cancel/竞态/隔离）由 test_api_stop.py 直接调用端点覆盖。
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.api.agent import _build_assistant_content
from app.main import app
from app.models import message as message_model
from app.models import session as session_model


@pytest.fixture(autouse=True)
async def fresh_db(clear_tables):
    """每用例前清空测试库三表，保证数据隔离。"""
    yield


@pytest.fixture()
def client():
    return TestClient(app)


def _parse_sse(chunk: str) -> tuple[str, dict]:
    """解析一段 SSE 消息为 (event, data)。"""
    lines = chunk.strip().splitlines()
    event = lines[0].removeprefix("event: ")
    data = json.loads(lines[1].removeprefix("data: "))
    return event, data


def test_build_assistant_content():
    assert _build_assistant_content({"final_message": "完成", "final_code": ""}) == "完成"
    assert "```python" in _build_assistant_content(
        {"final_message": "完成", "final_code": "print(1)"}
    )


def test_agent_missing_task_desc(client):
    assert client.post("/api/agent", json={"task_desc": "  "}).status_code == 400


def test_agent_invalid_session_404(client):
    resp = client.post(
        "/api/agent", json={"task_desc": "任务", "session_id": "nonexistent"}
    )
    assert resp.status_code == 404


def test_confirmation_requires_session(client):
    resp = client.post(
        "/api/agent",
        json={"confirmation": {"run_id": "r1", "approved": True}},
    )
    assert resp.status_code == 400


def test_confirmation_invalid_session_404(client):
    resp = client.post(
        "/api/agent",
        json={
            "session_id": "nonexistent",
            "confirmation": {"run_id": "r1", "approved": True},
        },
    )
    assert resp.status_code == 404


def test_agent_full_stream_and_persist(client, monkeypatch):
    """全链路：SSE 事件流（start→node→done）+ 会话/消息/步骤落库。"""

    async def fake_arun(task_desc, **kwargs):
        # 模拟编排器推送节点事件后交付
        for cb_event in [{"type": "node", "node": "react_node", "update": {"思考中": True}}]:
            await kwargs["on_event"](cb_event)
        return {
            "final_code": "print('done')",
            "final_message": "任务完成",
            "thinking": [],
            "tests_passed": True,
            "reflection_count": 1,
            "test_results": {"passed": 2},
            "model": "test-model",
            "elapsed_ms": 100,
        }

    monkeypatch.setattr("app.api.agent.orchestrator.arun", fake_arun)

    with client.stream("POST", "/api/agent", json={"task_desc": "写个程序"}) as resp:
        assert resp.status_code == 200
        events = []
        for chunk in resp.iter_lines():
            if chunk.startswith("event: "):
                events.append(chunk.removeprefix("event: "))

    assert events[0] == "agent_start"
    assert "node" in events and events[-1] == "done"

    # 落库校验：会话完成、用户+助手消息、执行步骤
    sessions = asyncio.get_event_loop().run_until_complete(session_model.list_sessions())
    sid = sessions[0]["session_id"]
    assert sessions[0]["status"] == "completed"
    assert "print('done')" in sessions[0]["final_code"]

    messages = asyncio.get_event_loop().run_until_complete(
        message_model.get_messages_by_session(sid)
    )
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["thinking"]["tests_passed"] is True


def test_agent_stop_unknown_run_id(client):
    """停止不存在的 run_id：幂等返回 stopped=False。"""
    resp = client.post("/api/agent/stop", json={"run_id": "nonexistent"})
    assert resp.status_code == 200 and resp.json()["stopped"] is False
