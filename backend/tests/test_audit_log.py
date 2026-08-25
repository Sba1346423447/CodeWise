"""审计日志测试：main.AuditLogMiddleware 记录 用户(IP)/动作/结果，/health 不记录。

通过 loguru 临时 sink 捕获 audit 标记的记录（sink 全局共享，
bind(audit=True) 的事件会被 filter 命中）。
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

from app.main import AuditLogMiddleware


def _audit_client():
    """挂载审计中间件的独立应用。"""
    audit_app = FastAPI()

    @audit_app.get("/api/ping")
    async def ping():
        return {"ok": True}

    @audit_app.get("/health")
    async def health():
        return {"status": "ok"}

    audit_app.add_middleware(AuditLogMiddleware)
    return TestClient(audit_app)


def _capture_audit():
    """捕获 audit 标记的日志消息，返回 (messages, sink_id)。"""
    messages: list[str] = []
    sink_id = logger.add(
        lambda msg: messages.append(str(msg)),
        filter=lambda record: record["extra"].get("audit") is True,
        level="INFO",
    )
    return messages, sink_id


def test_audit_log_records_request():
    messages, sink_id = _capture_audit()
    try:
        client = _audit_client()
        assert client.get("/api/ping").status_code == 200
    finally:
        logger.remove(sink_id)

    assert len(messages) == 1
    assert "GET /api/ping" in messages[0]
    assert "-> 200" in messages[0]


def test_audit_log_skips_health_probe():
    """/health 探活高频访问，不计入审计避免噪音。"""
    messages, sink_id = _capture_audit()
    try:
        client = _audit_client()
        assert client.get("/health").status_code == 200
    finally:
        logger.remove(sink_id)

    assert messages == []
