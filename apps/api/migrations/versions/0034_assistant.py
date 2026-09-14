"""First-party assistant history and durable operation previews."""

from alembic import op
import sqlalchemy as sa

revision = "0034_assistant"
down_revision = "0033_row_review_pending"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assistant_threads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("profile_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_assistant_threads_updated_at", "assistant_threads", ["updated_at"])
    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "thread_id",
            sa.String(36),
            sa.ForeignKey("assistant_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.UniqueConstraint("thread_id", "position", name="uq_assistant_message_position"),
        sa.Column("parts_json", sa.Text, nullable=False),
        sa.Column("context_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_messages_thread_id", "assistant_messages", ["thread_id"])
    op.create_table(
        "assistant_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "thread_id",
            sa.String(36),
            sa.ForeignKey("assistant_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("assistant_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("profile_id", sa.String(36), nullable=False),
        sa.Column("profile_version", sa.Integer, nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("usage_json", sa.Text, nullable=False),
        sa.Column("events_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_runs_thread_id", "assistant_runs", ["thread_id"])
    op.create_index("ix_assistant_runs_status", "assistant_runs", ["status"])
    op.create_table(
        "assistant_tool_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("assistant_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("arguments_json", sa.Text, nullable=False),
        sa.Column("result_json", sa.Text, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_tool_calls_run_id", "assistant_tool_calls", ["run_id"])


def downgrade():
    for name in [
        "assistant_tool_calls",
        "assistant_runs",
        "assistant_messages",
        "assistant_threads",
    ]:
        op.drop_table(name)
