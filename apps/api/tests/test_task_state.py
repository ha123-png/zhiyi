import pytest

from document_pipeline_api.domain.tasks import (
    InvalidTaskTransition,
    TaskState,
    TaskStatus,
)


def test_happy_path_reaches_completed() -> None:
    state = TaskState()

    for target in (
        TaskStatus.QUEUED,
        TaskStatus.PROCESSING,
        TaskStatus.VALIDATING,
        TaskStatus.COMPLETED,
    ):
        state = state.transition_to(target)

    assert state.status is TaskStatus.COMPLETED


def test_task_can_pause_and_resume_through_queue() -> None:
    state = TaskState(status=TaskStatus.PROCESSING)

    paused = state.transition_to(TaskStatus.PAUSED)
    resumed = paused.transition_to(TaskStatus.QUEUED)

    assert resumed.status is TaskStatus.QUEUED


def test_completed_task_is_terminal() -> None:
    state = TaskState(status=TaskStatus.COMPLETED)

    with pytest.raises(InvalidTaskTransition):
        state.transition_to(TaskStatus.PROCESSING)


def test_review_returns_to_validation_after_edit() -> None:
    state = TaskState(status=TaskStatus.NEEDS_REVIEW)

    next_state = state.transition_to(TaskStatus.VALIDATING)

    assert next_state.status is TaskStatus.VALIDATING
