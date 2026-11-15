from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.schemas.input_scope import InputScope
from document_pipeline_api.schemas.file_export import TaskExportState
from document_pipeline_api.schemas.file_name import FileNameRead


MAX_TASK_QUERY_IDS = 100
TaskQueryId = Annotated[str, Field(min_length=1, max_length=64)]
TaskGroup = Literal["all", "active", "pending", "completed", "failed", "cancelled"]
TaskPendingReason = Literal["template", "input_scope", "target_table", "unknown"]
TaskPendingAction = Literal["select_template", "confirm_input_scope", "confirm_target_table"]


class TaskGroupCounts(BaseModel):
    all: int = 0
    active: int = 0
    pending: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0


class TaskRetrySkipped(BaseModel):
    task_id: str
    reason: Literal["not_found", "not_failed", "state_changed", "capacity", "rejected", "duplicate"]
    message: str


class TaskBatchRetryResult(BaseModel):
    retried: list[str] = Field(default_factory=list)
    skipped: int = 0
    skipped_tasks: list[TaskRetrySkipped] = Field(default_factory=list)


class TaskTemplateCandidate(BaseModel):
    id: str
    version: int
    name: str
    description: str


class TaskTemplateSelection(BaseModel):
    template_id: str | None = Field(default=None, min_length=1, max_length=64)
    target_table_id: str | None = Field(default=None, min_length=1, max_length=64)
    expected_review_version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def one_target(self):
        if bool(self.template_id) == bool(self.target_table_id):
            raise ValueError("请选择一个模板或数据表。")
        return self


class TasksSummary(BaseModel):
    """历史页统计条与 tab 计数：SQL 聚合返回，不加载任务全表。"""

    total: int = 0
    completed: int = 0
    needs_review: int = 0
    failed: int = 0
    cancelled: int = 0
    active: int = 0
    waiting_for_action: int = 0
    pending_exports: int = 0
    group_counts: TaskGroupCounts = Field(default_factory=TaskGroupCounts)


class TaskRead(BaseModel):
    autonomous_fallback: bool = False
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    content_type: str
    size_bytes: int
    page_count: int
    planned_scope: InputScope | None = None
    match_scope: InputScope | None = None
    pending_reason: TaskPendingReason | None = None
    pending_action: TaskPendingAction | None = None
    pending_review_version: int | None = None
    has_result: bool = False
    archive_pending: bool = False
    file_export: TaskExportState | None = None
    file_name: FileNameRead | None = None
    internal_storage: dict | None = None
    sha256: str
    template_mode: str
    template_id: str | None
    template_version: int | None
    candidate_templates: list[TaskTemplateCandidate]
    status: TaskStatus
    attempt_count: int
    failure_code: str | None
    failure_message: str | None
    duplicate_of_task_id: str | None
    record_count: int = 0
    processing_elapsed_seconds: float | None = None
    processing_engine: str | None = None
    processing_model: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def resolve_pending_action(cls, value, info: ValidationInfo):
        from document_pipeline_api.models.task import TaskRecord
        if isinstance(value, TaskRecord):
            from document_pipeline_api.services.task_presentation import pending_details_for_task
            details = (info.context or {}).get("pending_details", {}).get(value.id)
            if details is None:
                details = pending_details_for_task(value)
            fields = {
                name: getattr(value, name)
                for name in cls.model_fields
                if hasattr(value, name)
            }
            return {**fields, **details}
        return value
