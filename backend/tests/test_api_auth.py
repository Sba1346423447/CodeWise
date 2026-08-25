"""P0 鉴权与限流测试：API Key 校验（api/deps.py）+ 滑动窗口限流（main.RateLimitMiddleware）。

鉴权逻辑在请求期读取 os.environ["API_KEY"]，monkeypatch 环境变量即可覆盖；
限流中间件以独立 FastAPI 应用实例化小窗口参数，验证 429 与 Retry-After 头。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import RateLimitMiddleware, app


@pytest.fixture()
def client():
    return TestClient(app)


# ---------- API Key 鉴权 ----------


def test_auth_missing_key_returns_401(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-key")
    resp = client.get("/api/sessions")
    assert resp.status_code == 401


def test_auth_wrong_key_returns_401(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-key")
    resp = client.get("/api/sessions", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_auth_correct_key_passes(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-key")
    resp = client.get("/api/sessions", headers={"X-API-Key": "secret-key"})
    assert resp.status_code == 200


def test_auth_disabled_when_api_key_unset(client, monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    resp = client.get("/api/sessions")
    assert resp.status_code == 200


def test_health_exempt_from_auth(client, monkeypatch):
    """健康检查不挂鉴权依赖：Docker healthcheck / LB 探活匿名可用。"""
    monkeypatch.setenv("API_KEY", "secret-key")
    resp = client.get("/health")
    assert resp.status_code == 200


# ---------- 滑动窗口限流 ----------


@pytest.fixture()
def limited_client():
    """独立小窗口应用：60 秒内最多 2 次 POST /api/agent。"""
    limited_app = FastAPI()

    @limited_app.post("/api/agent")
    async def stub_agent():
        return {"ok": True}

    @limited_app.get("/api/sessions")
    async def stub_sessions():
        return []

    limited_app.add_middleware(RateLimitMiddleware, times=2, window=60.0)
    return TestClient(limited_app)


def test_rate_limit_blocks_after_threshold(limited_client):
    for _ in range(2):
        assert limited_client.post("/api/agent").status_code == 200
    blocked = limited_client.post("/api/agent")
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_rate_limit_only_targets_agent_endpoint(limited_client):
    """非 /api/agent 接口不限流：会话列表等轻量接口不受影响。"""
    for _ in range(5):
        assert limited_client.get("/api/sessions").status_code == 200
