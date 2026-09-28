"""A file's current header and durable, file-level editing history."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from document_pipeline_api.db import Base
from document_pipeline_api.models.task import TaskRecord, utc_now


class FileCurrentDataRecord(Base):
    __tablename__ = "file_current_data"

    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    table_id: Mapped[str | None] = mapped_column(ForeignKey("data_tables.id"), nullable=True, index=True)
    # Header appears here once. DataRowRecord contains details only for file rows.
    header_json: Mapped[str] = mapped_column(Text, default="{}")
    schema_json: Mapped[str] = mapped_column(Text, default="[]")
    validation_json: Mapped[str] = mapped_column(Text, default="[]")
    has_data: Mapped[bool] = mapped_column(Boolean, default=True)
    # Optimistic-lock token, independent of the user-facing sealed revision number.
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    next_item_index: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    source: Mapped[TaskRecord] = relationship(TaskRecord, lazy="joined", viewonly=True)


class DataEditSessionRecord(Base):
    __tablename__ = "data_edit_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    editor: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FileRevisionRecord(Base):
    __tablename__ = "file_revisions"
    __table_args__ = (UniqueConstraint("task_id", "version", name="uq_file_revision_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("data_edit_sessions.id"), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(32))
    editor: Mapped[str] = mapped_column(String(64))
    schema_json: Mapped[str] = mapped_column(Text)
    # File-level deltas only; details are separate pageable history records.
    # No current reader may fall back to any revision.
    changes_json: Mapped[str] = mapped_column(Text)
    modified_fields: Mapped[int] = mapped_column(Integer, default=0)
    deleted_details: Mapped[int] = mapped_column(Integer, default=0)
    added_details: Mapped[int] = mapped_column(Integer, default=0)
    total_details: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FileRevisionDetailRecord(Base):
    __tablename__ = "file_revision_details"
    __table_args__ = (Index("ix_file_revision_detail_order", "revision_id", "item_index", "row_id"),)

    revision_id: Mapped[str] = mapped_column(ForeignKey("file_revisions.id", ondelete="CASCADE"), primary_key=True)
    # Historical identity intentionally has no FK to the deletable current row.
    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_index: Mapped[int] = mapped_column(Integer)
    change_json: Mapped[str] = mapped_column(Text)


class PendingOriginalDeletionRecord(Base):
    """Durable cleanup obligation, committed with deletion of its former owner."""
    __tablename__ = "pending_original_deletions"

    relative_path: Mapped[str] = mapped_column(String(1024), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)  # Owner is intentionally deleted.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
