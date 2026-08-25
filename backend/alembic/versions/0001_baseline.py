"""基线迁移：sessions / steps / messages 三表（MySQL 方言，utf8mb4）。

对应原 SQLite schema v1+v2 合并基线：
- sessions：会话（含最终代码与状态）
- steps：Agent 执行步骤（外键 sessions）
- messages：会话内多轮对话消息（外键 sessions，thinking 存 JSON 字符串）

Revision ID: 0001
Revises:
Create Date: 2026-08-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME, MEDIUMTEXT

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column:
    """创建时间列：DATETIME(3) 毫秒精度 + 数据库默认值。

    毫秒精度保证同秒插入的多条消息/步骤按 created_at 排序稳定
    （秒级精度下同秒记录排序不确定，破坏对话回放顺序）。
    """
    return sa.Column(
        "created_at",
        DATETIME(fsp=3),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP(3)"),
    )


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.String(32), primary_key=True),
        sa.Column("task_desc", sa.Text, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("final_code", MEDIUMTEXT),
        _created_at(),
    )
    op.create_table(
        "steps",
        sa.Column("step_id", sa.String(32), primary_key=True),
        sa.Column("session_id", sa.String(32), nullable=False),
        sa.Column("step_type", sa.String(32), nullable=False),
        sa.Column("input", sa.Text),
        sa.Column("output", sa.Text),
        _created_at(),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
    )
    op.create_index("ix_steps_session_id", "steps", ["session_id"])
    op.create_table(
        "messages",
        sa.Column("message_id", sa.String(32), primary_key=True),
        sa.Column("session_id", sa.String(32), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", MEDIUMTEXT, nullable=False),
        sa.Column("thinking", sa.Text),
        _created_at(),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_session_id", "messages")
    op.drop_table("messages")
    op.drop_index("ix_steps_session_id", "steps")
    op.drop_table("steps")
    op.drop_table("sessions")
