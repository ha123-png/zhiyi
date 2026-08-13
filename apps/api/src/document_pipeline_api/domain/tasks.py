from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class TaskStatus(StrEnum):
    CREATED = "created"
    WAITING_FOR_TEMPLATE = "waiting_for_template"
    QUEUED = "queued"
    PROCESSING = "processing"
    VALIDATING = "validating"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset(
        {TaskStatus.WAITING_FOR_TEMPLATE, TaskStatus.QUEUED, TaskStatus.CANCELLED}
    ),
    TaskStatus.WAITING_FOR_TEMPLATE: frozenset(
        {TaskStatus.QUEUED, TaskStatus.COMPLETED, TaskStatus.CANCELLED}
    ),
    TaskStatus.QUEUED: frozenset(
        {TaskStatus.PROCESSING, TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.FAILED}
    ),
    TaskStatus.PROCESSING: frozenset(
        {
            TaskStatus.WAITING_FOR_TEMPLATE,
            TaskStatus.VALIDATING,
            TaskStatus.PAUSED,
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.VALIDATING: frozenset(
        {
            TaskStatus.WAITING_FOR_TEMPLATE,
            TaskStatus.NEEDS_REVIEW,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.NEEDS_REVIEW: frozenset(
        {TaskStatus.VALIDATING, TaskStatus.COMPLETED, TaskStatus.CANCELLED}
    ),
    TaskStatus.PAUSED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
}


class InvalidTaskTransition(ValueError):
    def __init__(self, current: TaskStatus, target: TaskStatus) -> None:
        super().__init__(f"Task cannot move from {current.value} to {target.value}")
        self.current = current
        self.target = target


class TaskState(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: TaskStatus = TaskStatus.CREATED

    def transition_to(self, target: TaskStatus) -> "TaskState":
        if target not in ALLOWED_TRANSITIONS[self.status]:
            raise InvalidTaskTransition(self.status, target)
        return TaskState(status=target)
