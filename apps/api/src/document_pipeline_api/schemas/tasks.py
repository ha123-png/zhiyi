from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from document_pipeline_api.domain.tasks import TaskStatus


class TaskTemplateCandidate(BaseModel):
    id: str
    version: int
    name: str
    description: str


class TaskTemplateSelection(BaseModel):
    template_id: str | None = Field(default=None, min_length=1, max_length=64)
    target_table_id: str | None = Field(default=None, min_length=1, max_length=64)


class TasksSummary(BaseModel):
    """历史页统计条与 tab 计数：SQL 聚合返回，不加载任务全表。"""

    total: int = 0
    completed: int = 0
    needs_review: int = 0
    failed: int = 0


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    content_type: str
    size_bytes: int
    page_count: int
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
