"""Smart-match pre-selection pool flag on templates.

Revision ID: 0019_template_smart_pool
Revises: 0018_task_target_table
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0019_template_smart_pool"
down_revision: str | Sequence[str] | None = "0018_task_target_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 智能匹配预选池：默认全部模板都在池内；取消勾选的模板不再参与智能匹配候选。
    op.add_column(
        "templates",
        sa.Column(
            "in_smart_pool",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("templates", "in_smart_pool")
