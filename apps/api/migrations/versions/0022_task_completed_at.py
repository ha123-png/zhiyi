"""Task completion timestamp for history and extract pages.

Revision ID: 0022_task_completed_at
Revises: 0021_task_list_scale
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0022_task_completed_at"
down_revision: str | Sequence[str] | None = "0021_task_list_scale"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 任务完成时间：进入 completed 状态时写入；历史页与提取页据此展示"完成时间"。
    op.add_column(
        "tasks",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 存量已完成任务：无历史完成时间，用 updated_at 尽力回填（新任务在确认时写入）
    op.execute("UPDATE tasks SET completed_at = updated_at WHERE status = 'completed'")


def downgrade() -> None:
    op.drop_column("tasks", "completed_at")
