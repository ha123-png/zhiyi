"""Separate local export bindings and immutable task destination snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0027_local_exports"
down_revision = "0026_input_scopes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "template_local_bindings",
        sa.Column("template_id", sa.String(36), sa.ForeignKey("templates.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("parent_path", sa.String(2048), nullable=True),
    )
    op.add_column("tasks", sa.Column("export_state_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "export_state_json")
    op.drop_table("template_local_bindings")
