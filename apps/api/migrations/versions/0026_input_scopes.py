"""Persist task input policy and successful extraction coverage independently."""
from alembic import op
import sqlalchemy as sa

revision = "0026_input_scopes"
down_revision = "0025_table_presentation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("input_policy_json", "input_plan_json", "match_scope_json"):
        op.add_column("tasks", sa.Column(name, sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("processing_units", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("pending_reason", sa.String(32), nullable=True))
    op.add_column("extractions", sa.Column("input_scope_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("extractions", "input_scope_json")
    for name in ("pending_reason", "processing_units", "match_scope_json", "input_plan_json", "input_policy_json"):
        op.drop_column("tasks", name)
