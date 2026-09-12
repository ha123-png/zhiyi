"""Stable local template folders and recoverable original relocation status."""
from alembic import op
import sqlalchemy as sa

revision = "0029_internal_storage"
down_revision = "0028_file_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("template_local_bindings", sa.Column("internal_folder", sa.String(180), nullable=True))
    op.create_index("uq_internal_template_folder", "template_local_bindings", ["internal_folder"], unique=True)
    op.add_column("tasks", sa.Column("internal_storage_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "internal_storage_json")
    op.drop_index("uq_internal_template_folder", "template_local_bindings")
    op.drop_column("template_local_bindings", "internal_folder")
