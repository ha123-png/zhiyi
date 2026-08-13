"""Add independent extraction evidence records.

Revision ID: 0009_extraction_evidence
Revises: 0008_task_page_counts
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009_extraction_evidence"
down_revision: str | Sequence[str] | None = "0008_task_page_counts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("extractions") as batch:
        batch.add_column(
            sa.Column(
                "evidence_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("extractions") as batch:
        batch.drop_column("evidence_json")
