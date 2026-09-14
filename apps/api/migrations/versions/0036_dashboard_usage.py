"""Portable dashboard definitions and inference accounting, without source text."""
from alembic import op
import sqlalchemy as sa

revision = "0036_dashboard_usage"
down_revision = "0035_assistant_native_context"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("dashboard_cards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("model_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("usage_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_model_usage_created_at", "model_usage", ["created_at"])


def downgrade():
    op.drop_table("model_usage")
    op.drop_table("dashboard_cards")
