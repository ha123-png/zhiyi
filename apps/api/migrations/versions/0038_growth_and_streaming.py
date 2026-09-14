"""Durable naming counters and append-only conversation events."""
from alembic import op
import sqlalchemy as sa

revision = "0038_growth_and_streaming"
down_revision = "0037_model_context_policy"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("file_name_sequences",
        sa.Column("template_id", sa.String(36), primary_key=True),
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("value", sa.Integer, nullable=False))
    op.add_column("assistant_runs", sa.Column("stream_version", sa.Integer, nullable=False, server_default="0"))
    op.add_column("assistant_runs", sa.Column("snapshot_sequence", sa.Integer, nullable=False, server_default="0"))
    op.create_table("assistant_events",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("assistant_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sequence", sa.Integer, primary_key=True),
        sa.Column("payload_json", sa.Text, nullable=False))


def downgrade():
    op.drop_table("assistant_events")
    op.drop_column("assistant_runs", "snapshot_sequence")
    op.drop_column("assistant_runs", "stream_version")
    op.drop_table("file_name_sequences")
