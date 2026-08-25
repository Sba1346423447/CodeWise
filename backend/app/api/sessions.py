"""会话管理接口：GET/POST/DELETE /api/sessions，历史会话 CRUD。

依赖：fastapi（路由）、models.*（会话 / 步骤 / 消息数据访问层）。
"""


from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..models import message as message_model
from ..models import session as session_model
from ..models import step as step_model

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    """创建会话请求体。"""

    task_desc: str


@router.get("/")
async def list_sessions() -> list[dict]:
    """列出全部历史会话（按创建时间倒序，供左侧会话栏渲染）。"""
    return await session_model.list_sessions()


@router.get("/{session_id}")
async def get_session(session_id: str) -> dict:
    """查询单个会话详情，附带执行步骤时间线。"""
    session = await session_model.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    session["steps"] = await step_model.get_steps_by_session(session_id)
    # 附带多轮对话消息（用户/助手），供历史会话回放
    session["messages"] = await message_model.get_messages_by_session(session_id)
    return session


@router.post("/")
async def create_session(request: CreateSessionRequest) -> dict:
    """创建新会话（仅保存任务描述；Agent 执行由 POST /api/agent 触发）。"""
    task_desc = request.task_desc.strip()
    if not task_desc:
        raise HTTPException(status_code=400, detail="task_desc 不能为空")
    return await session_model.create_session(task_desc)


class RenameSessionRequest(BaseModel):
    """重命名会话请求体。"""

    task_desc: str


@router.patch("/{session_id}")
async def rename_session(session_id: str, request: RenameSessionRequest) -> dict:
    """重命名会话（更新任务描述，供前端标题栏重命名操作）。"""
    session = await session_model.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    task_desc = request.task_desc.strip()
    if not task_desc:
        raise HTTPException(status_code=400, detail="task_desc 不能为空")
    await session_model.rename_session(session_id, task_desc)
    updated = await session_model.get_session(session_id)
    return updated or {}


@router.delete("/{session_id}")
async def delete_session(session_id: str) -> dict:
    """删除会话及其关联步骤。"""
    session = await session_model.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    await session_model.delete_session(session_id)
    return {"deleted": session_id}
