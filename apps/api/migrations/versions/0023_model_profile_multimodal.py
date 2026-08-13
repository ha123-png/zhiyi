"""Model profile multimodal capability flag.

Revision ID: 0023_model_profile_multimodal
Revises: 0022_task_completed_at
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0023_model_profile_multimodal"
down_revision: str | Sequence[str] | None = "0022_task_completed_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 方案是否支持图片识别：None=未验证，True=支持，False=仅文本。
    # 仅文本方案处理图片类输入时任务直接失败并给出可操作提示，不再静默产出垃圾结果。
    op.add_column(
        "model_profile_versions",
        sa.Column("multimodal", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_profile_versions", "multimodal")
