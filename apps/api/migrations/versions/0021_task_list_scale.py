"""Task list pagination and scale indexes.

Revision ID: 0021_task_list_scale
Revises: 0020_task_started_at
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0021_task_list_scale"
down_revision: str | Sequence[str] | None = "0020_task_started_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 任务列表按 created_at 倒序分页；状态卡按 status 过滤 + 时间排序。
    # 复合索引同时服务"按状态筛 + 按时间排"与纯时间倒序（前缀 created_at 无单独索引时
    # SQLite 无法利用该复合索引做纯 created_at 排序，故各建一个）。
    op.create_index(
        "ix_tasks_status_created_at",
        "tasks",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_created_at",
        "tasks",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_created_at", table_name="tasks")
    op.drop_index("ix_tasks_status_created_at", table_name="tasks")
