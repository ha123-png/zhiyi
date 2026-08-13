from datetime import datetime, timezone
import json

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from document_pipeline_api.db import Base
from document_pipeline_api.domain.tasks import TaskStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_path: Mapped[str] = mapped_column(String(1024))
    template_mode: Mapped[str] = mapped_column(String(32), default="smart")
    template_id: Mapped[str | None] = mapped_column(
        ForeignKey("templates.id"),
        nullable=True,
    )
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_templates_json: Mapped[str] = mapped_column(Text, default="[]")
    model_config_version: Mapped[str] = mapped_column(
        String(32),
        default="legacy-unversioned",
    )
    model_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_profiles.id"),
        nullable=True,
    )
    model_profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_reasoning_effort: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_timeout_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_context_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_secret_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.QUEUED.value, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duplicate_of_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id"),
        nullable=True,
    )
    # 提取结果物化目标表；NULL 表示默认的模板唯一表。普通字符串列不设外键，
    # 目标表被删除后物化时检测并回退到模板表重建。
    target_table_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 本次处理尝试的开始时间：上传=创建时间，重试/恢复/选模板后重置，
    # 任务真正开始处理（acquire 租约）时再次重置；
    # 前端全局任务条计时以此为准（重试后从零重新计时）。
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # 任务完成时间：进入 completed 状态时写入；未完成/失败任务为 NULL。
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )

    @property
    def candidate_templates(self) -> list[dict[str, str | int]]:
        return json.loads(self.candidate_templates_json)
