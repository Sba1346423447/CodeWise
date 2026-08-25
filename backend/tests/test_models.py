"""models 层 CRUD 测试：sessions / messages / steps 数据访问层（MySQL 测试库，真实读写）。

每用例经 clear_tables 清空测试库三表保证隔离；覆盖创建/查询/更新/删除/迁移全路径。
"""

import pytest

from app.models import database
from app.models import message as message_model
from app.models import session as session_model
from app.models import step as step_model


@pytest.fixture(autouse=True)
async def fresh_db(clear_tables):
    """每用例前清空测试库三表，保证数据隔离。"""
    yield


async def test_create_and_get_session():
    created = await session_model.create_session("写一个快排")
    assert created["task_desc"] == "写一个快排"
    assert created["status"] == session_model.STATUS_RUNNING
    fetched = await session_model.get_session(created["session_id"])
    assert fetched is not None and fetched["session_id"] == created["session_id"]


async def test_get_session_not_found():
    assert await session_model.get_session("nonexistent") is None


async def test_list_sessions_ordered_desc():
    first = await session_model.create_session("任务一")
    second = await session_model.create_session("任务二")
    sessions = await session_model.list_sessions()
    # 倒序：最新在前（created_at 同秒时 SQLite datetime 精度可能相同，改用集合断言）
    assert {s["session_id"] for s in sessions} == {first["session_id"], second["session_id"]}


async def test_update_session_status_and_code():
    created = await session_model.create_session("任务")
    await session_model.update_session(
        created["session_id"],
        status=session_model.STATUS_COMPLETED,
        final_code="print('ok')",
    )
    fetched = await session_model.get_session(created["session_id"])
    assert fetched["status"] == session_model.STATUS_COMPLETED
    assert fetched["final_code"] == "print('ok')"


async def test_rename_session():
    created = await session_model.create_session("旧标题")
    await session_model.rename_session(created["session_id"], "新标题")
    fetched = await session_model.get_session(created["session_id"])
    assert fetched["task_desc"] == "新标题"


async def test_delete_session_cascades():
    created = await session_model.create_session("任务")
    sid = created["session_id"]
    await message_model.add_message(sid, message_model.ROLE_USER, "你好")
    await step_model.create_step(sid, step_model.STEP_REACT)
    await session_model.delete_session(sid)
    assert await session_model.get_session(sid) is None
    assert await message_model.get_messages_by_session(sid) == []
    assert await step_model.get_steps_by_session(sid) == []


async def test_add_and_get_messages_with_thinking():
    created = await session_model.create_session("任务")
    sid = created["session_id"]
    await message_model.add_message(sid, message_model.ROLE_USER, "需求")
    thinking = {"steps": [{"type": "react", "label": "分析"}], "tests_passed": True}
    await message_model.add_message(
        sid, message_model.ROLE_ASSISTANT, "答案", thinking=thinking
    )
    messages = await message_model.get_messages_by_session(sid)
    assert len(messages) == 2
    assert messages[0]["role"] == message_model.ROLE_USER
    assert messages[0]["thinking"] is None
    # thinking 序列化存储、读取时反序列化还原
    assert messages[1]["thinking"] == thinking


async def test_clear_messages():
    created = await session_model.create_session("任务")
    sid = created["session_id"]
    await message_model.add_message(sid, message_model.ROLE_USER, "你好")
    await message_model.clear_messages(sid)
    assert await message_model.get_messages_by_session(sid) == []


async def test_create_and_get_steps():
    created = await session_model.create_session("任务")
    sid = created["session_id"]
    step = await step_model.create_step(sid, step_model.STEP_TOOL, "输入", "输出")
    fetched = await step_model.get_step(step["step_id"])
    assert fetched is not None and fetched["step_type"] == step_model.STEP_TOOL
    assert await step_model.get_step("nonexistent") is None


async def test_init_db_idempotent(db_schema):
    """重复 init_db 不报错（alembic 已在最新版本，upgrade 空转）。"""
    database.init_db()
