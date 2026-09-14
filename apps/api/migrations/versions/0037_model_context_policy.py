"""Distinguish automatic request budgets from explicitly configured capacities."""
from alembic import op
import sqlalchemy as sa

revision = "0037_model_context_policy"
down_revision = "0036_dashboard_usage"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("model_profile_versions", sa.Column("context_policy", sa.String(16), nullable=False, server_default="fixed"))
    # The old form silently assigned 8192 to remote presets and offered no editor.
    # Preserve all stored lengths and task snapshots; repair only that legacy default.
    op.execute("UPDATE model_profile_versions SET context_policy='auto' WHERE is_remote=1 AND context_length=8192")


def downgrade():
    op.drop_column("model_profile_versions", "context_policy")
