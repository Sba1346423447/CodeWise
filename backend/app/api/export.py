"""导出接口：GET /api/export/{session_id}，导出会话结果为 Markdown/JSON。

依赖：fastapi（路由 / 响应类型）、models.*（会话与步骤数据访问层）。
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse

from ..models import session as session_model
from ..models import step as step_model

router = APIRouter(prefix="/api/export", tags=["export"])


@router.get("/{session_id}")
async def export_session(
    session_id: str,
    output_format: str = Query(
        "markdown", alias="format", pattern="^(markdown|json)$"
    ),
):
    """导出会话结果：?format=markdown（默认）或 json；会话不存在返回 404。"""
    session = await session_model.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    steps = await step_model.get_steps_by_session(session_id)

    if output_format == "json":
        # MySQL 驱动返回 datetime 对象，经 jsonable_encoder 转 ISO 字符串（前端 new Date 可解析）
        return JSONResponse(jsonable_encoder({"session": session, "steps": steps}))

    return PlainTextResponse(
        _to_markdown(session, steps), media_type="text/markdown"
    )


def _to_markdown(session: dict, steps: list) -> str:
    """将会话信息与执行步骤组装为 Markdown 文档（含最终代码）。"""
    lines = [
        f"# {session['task_desc']}",
        "",
        f"- 会话 ID：`{session['session_id']}`",
        f"- 创建时间：{session['created_at']}",
        f"- 状态：{session['status']}",
        "",
        "## 执行步骤",
        "",
    ]
    for idx, step in enumerate(steps, start=1):
        lines.append(f"### {idx}. {step['step_type']}")
        if step.get("output"):
            lines.append(f"```json\n{step['output']}\n```")
        lines.append("")

    lines.append("## 最终代码")
    lines.append("")
    lines.append(f"```python\n{session.get('final_code') or ''}\n```")
    return "\n".join(lines)
