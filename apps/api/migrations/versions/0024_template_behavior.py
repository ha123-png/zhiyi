"""Portable, versioned presentation and processing behavior.

Existing templates retain table presentation, complete-input requirements and
original filenames. No local export destination belongs in this JSON contract.
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_template_behavior"
down_revision = "0023_model_profile_multimodal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "template_versions",
        sa.Column("behavior_json", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("template_versions", "behavior_json")
