from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from document_pipeline_api.db import Base
from document_pipeline_api.models.task import utc_now


class ReviewRevisionRecord(Base):
    __tablename__ = "review_revisions"
    __table_args__ = (
        UniqueConstraint("task_id", "version", name="uq_review_task_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    result_json: Mapped[str] = mapped_column(Text)
    validation_json: Mapped[str] = mapped_column(Text)
    rule_engine_version: Mapped[str] = mapped_column(
        String(32),
        default="legacy-unversioned",
    )
    changes_json: Mapped[str] = mapped_column(Text)
    editor: Mapped[str] = mapped_column(String(64), default="local-user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
