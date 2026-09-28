"""Fence assistant plans and runs when authorization or dataset changes."""

from alembic import op
import sqlalchemy as sa

revision = "0045_assistant_authorization"
down_revision = "0044_revision_details"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assistant_threads", sa.Column("authorization_json", sa.Text(), nullable=True))
    op.add_column("assistant_threads", sa.Column("authorization_revision", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("assistant_runs", sa.Column("authorization_revision", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("assistant_runs", sa.Column("dataset_generation", sa.String(36), nullable=True))


def downgrade():
    op.drop_column("assistant_runs", "dataset_generation")
    op.drop_column("assistant_runs", "authorization_revision")
    op.drop_column("assistant_threads", "authorization_revision")
    op.drop_column("assistant_threads", "authorization_json")
