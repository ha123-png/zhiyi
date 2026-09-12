from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from document_pipeline_api.db import Base
from document_pipeline_api.models.task import utc_now


class ExtractionRecord(Base):
    __tablename__ = "extractions"

    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    document_kind: Mapped[str] = mapped_column(String(32))
    template_id: Mapped[str | None] = mapped_column(
        ForeignKey("templates.id"),
        nullable=True,
    )
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_name: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32))
    rule_engine_version: Mapped[str] = mapped_column(
        String(32),
        default="legacy-unversioned",
    )
    elapsed_seconds: Mapped[float] = mapped_column(Float)
    result_json: Mapped[str] = mapped_column(Text)
    validation_json: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    input_scope_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
