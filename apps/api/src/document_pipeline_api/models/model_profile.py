from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from document_pipeline_api.db import Base
from document_pipeline_api.models.task import utc_now


class ModelProfileRecord(Base):
    __tablename__ = "model_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class ModelProfileVersionRecord(Base):
    __tablename__ = "model_profile_versions"
    __table_args__ = (
        UniqueConstraint("profile_id", "version", name="uq_model_profile_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("model_profiles.id"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(1024))
    model_name: Mapped[str] = mapped_column(String(128))
    reasoning_effort: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timeout_seconds: Mapped[float] = mapped_column(Float)
    context_length: Mapped[int] = mapped_column(Integer, default=8192)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    secret_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False)
    remote_data_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    # None=未验证；True=支持图片；False=仅文本（处理图片类输入时任务直接失败并提示）
    multimodal: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModelRuntimeStateRecord(Base):
    __tablename__ = "model_runtime_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_model_runtime_state_singleton"),
        CheckConstraint(
            "(active_profile_id IS NULL AND active_profile_version IS NULL) OR "
            "(active_profile_id IS NOT NULL AND active_profile_version IS NOT NULL)",
            name="ck_model_runtime_state_complete_pointer",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    active_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_profiles.id"),
        nullable=True,
    )
    active_profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
