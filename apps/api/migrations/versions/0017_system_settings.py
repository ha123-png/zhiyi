"""Add system settings key-value table for local preferences.

Revision ID: 0017_system_settings
Revises: 0016_model_profile_context
Create Date: 2026-08-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0017_system_settings"
down_revision: str | Sequence[str] | None = "0016_model_profile_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 本地偏好设置（如格式转换开关），只存布尔/短字符串，不入任务快照。
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
