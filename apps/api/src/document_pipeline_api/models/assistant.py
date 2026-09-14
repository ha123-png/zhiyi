"""Persistent first-party conversations. Business references are snapshots, not FKs."""

from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from document_pipeline_api.db import Base


def now():
    return datetime.now(timezone.utc)


class AssistantThread(Base):
    __tablename__ = "assistant_threads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "position", name="uq_assistant_message_position"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_threads.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    position: Mapped[int] = mapped_column(Integer)
    parts_json: Mapped[str] = mapped_column(Text, default="[]")
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    native_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AssistantRun(Base):
    __tablename__ = "assistant_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_threads.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[str] = mapped_column(ForeignKey("assistant_messages.id", ondelete="CASCADE"))
    profile_id: Mapped[str] = mapped_column(String(36))
    profile_version: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(256))
    provider: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_json: Mapped[str] = mapped_column(Text, default="{}")
    events_json: Mapped[str] = mapped_column(Text, default="[]")
    stream_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    snapshot_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AssistantEvent(Base):
    __tablename__ = "assistant_events"
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id", ondelete="CASCADE"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text)


class FileNameSequence(Base):
    __tablename__ = "file_name_sequences"
    template_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    value: Mapped[int] = mapped_column(Integer)


class AssistantToolCall(Base):
    __tablename__ = "assistant_tool_calls"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_runs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    arguments_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
