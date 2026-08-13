"""Add recoverable template archival.

Revision ID: 0011_template_archival
Revises: 0010_template_deterministic_rules
Create Date: 2026-08-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0011_template_archival"
down_revision: str | Sequence[str] | None = "0010_template_deterministic_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "templates",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("templates") as batch:
        batch.drop_column("is_active")
