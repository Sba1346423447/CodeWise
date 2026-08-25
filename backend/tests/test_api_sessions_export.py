"""API 层 sessions / export 接口测试：CRUD 全路径 + 导出双格式 + 404 分支。

走真实 HTTP 层与 MySQL 测试库（每用例清空三表，见 conftest.clear_tables）。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
async def fresh_db(clear_tables):
    """每用例前清空测试库三表，保证数据隔离。"""
    yield


@pytest.fixture()
def client():
    return TestClient(app)


def test_create_session_and_get_detail(client):
    created = client.post("/api/sessions", json={"task_desc": "写一个快排"})
    assert created.status_code == 200
    sid = created.json()["session_id"]

    detail = client.get(f"/api/sessions/{sid}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["task_desc"] == "写一个快排"
    assert body["steps"] == [] and body["messages"] == []


def test_create_session_empty_desc_rejected(client):
    assert client.post("/api/sessions", json={"task_desc": "   "}).status_code == 400


def test_get_session_404(client):
    assert client.get("/api/sessions/nonexistent").status_code == 404


def test_list_sessions(client):
    client.post("/api/sessions", json={"task_desc": "任务A"})
    client.post("/api/sessions", json={"task_desc": "任务B"})
    sessions = client.get("/api/sessions").json()
    assert {s["task_desc"] for s in sessions} == {"任务A", "任务B"}


def test_rename_session(client):
    sid = client.post("/api/sessions", json={"task_desc": "旧"}).json()["session_id"]
    renamed = client.patch(f"/api/sessions/{sid}", json={"task_desc": "新"})
    assert renamed.status_code == 200 and renamed.json()["task_desc"] == "新"
    # 重命名为空被拒
    assert client.patch(f"/api/sessions/{sid}", json={"task_desc": " "}).status_code == 400
    assert client.patch("/api/sessions/none", json={"task_desc": "x"}).status_code == 404


def test_delete_session(client):
    sid = client.post("/api/sessions", json={"task_desc": "待删"}).json()["session_id"]
    assert client.delete(f"/api/sessions/{sid}").json()["deleted"] == sid
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert client.delete(f"/api/sessions/{sid}").status_code == 404


def test_export_markdown_and_json(client):
    sid = client.post("/api/sessions", json={"task_desc": "导出任务"}).json()["session_id"]
    md = client.get(f"/api/export/{sid}")
    assert md.status_code == 200
    assert "# 导出任务" in md.text and "```python" in md.text

    js = client.get(f"/api/export/{sid}?format=json")
    assert js.status_code == 200
    assert js.json()["session"]["session_id"] == sid


def test_export_invalid_format_rejected(client):
    sid = client.post("/api/sessions", json={"task_desc": "x"}).json()["session_id"]
    assert client.get(f"/api/export/{sid}?format=xml").status_code == 422


def test_export_404(client):
    assert client.get("/api/export/nonexistent").status_code == 404
