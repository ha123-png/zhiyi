import json
import os
import tempfile
from pathlib import Path

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from document_pipeline_api.db import get_session
from document_pipeline_api.models import SystemSettingRecord, TaskRecord
from document_pipeline_api.models.system_setting import PENDING_DUPLICATE_ID_PATTERN, PENDING_DUPLICATE_PREFIX
from document_pipeline_api.services.read_snapshot import begin_write_snapshot

router = APIRouter(prefix="/user-state", tags=["user-state"])
SessionDependency = Annotated[Session, Depends(get_session)]


class OnboardingState(BaseModel):
    completed_version: int = 0


class PendingDuplicate(BaseModel):
    id: str = Field(pattern=PENDING_DUPLICATE_ID_PATTERN)
    name: str = Field(min_length=1, max_length=512)
    size: int = Field(ge=0)
    selection: str = Field(min_length=1, max_length=64)
    existingTaskId: str = Field(min_length=1, max_length=36)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    createdAt: datetime
    hasResult: bool = False


@router.get("/pending-duplicates", response_model=list[PendingDuplicate])
def list_pending_duplicates(session: SessionDependency):
    records = session.scalars(select(SystemSettingRecord.value).where(
        SystemSettingRecord.key.startswith(PENDING_DUPLICATE_PREFIX)).order_by(SystemSettingRecord.updated_at))
    result = []
    for value in records:
        try:
            result.append(PendingDuplicate.model_validate_json(value))
        except ValidationError:
            continue
    if not result:
        return result
    from document_pipeline_api.models import ExtractionRecord
    available = set(session.scalars(select(ExtractionRecord.task_id).where(
        ExtractionRecord.task_id.in_({item.existingTaskId for item in result}))))
    return [item.model_copy(update={"hasResult": item.existingTaskId in available}) for item in result]


@router.put("/pending-duplicates", response_model=PendingDuplicate)
def save_pending_duplicate(item: PendingDuplicate, session: SessionDependency):
    begin_write_snapshot(session)
    stored = session.get(TaskRecord, item.existingTaskId)
    if stored is None or (item.sha256 is not None and item.sha256 != stored.sha256):
        raise HTTPException(409, "已有文件已变化，请重新上传检查。")
    from document_pipeline_api.models import ExtractionRecord
    item = item.model_copy(update={"sha256": stored.sha256, "hasResult": session.get(ExtractionRecord, stored.id) is not None})
    key = PENDING_DUPLICATE_PREFIX + item.id
    session.execute(insert(SystemSettingRecord).values(key=key, value=item.model_dump_json())
                    .on_conflict_do_nothing(index_elements=["key"]))
    session.commit()
    return item


@router.delete("/pending-duplicates/{item_id}", status_code=204)
def delete_pending_duplicate(item_id: str, session: SessionDependency):
    session.execute(delete(SystemSettingRecord).where(SystemSettingRecord.key == PENDING_DUPLICATE_PREFIX + item_id))
    session.commit()


def _state_path(request: Request) -> Path:
    return request.app.state.settings.storage_dir.parent / "user-state.json"


@router.get("/onboarding", response_model=OnboardingState)
def get_onboarding(request: Request) -> OnboardingState:
    try:
        payload = json.loads(_state_path(request).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return OnboardingState(completed_version=0)
        version = payload.get("onboarding_completed_version", 0)
        return OnboardingState(completed_version=version if isinstance(version, int) else 0)
    except (OSError, TypeError, json.JSONDecodeError):
        return OnboardingState(completed_version=0)


@router.put("/onboarding", response_model=OnboardingState)
def update_onboarding(state: OnboardingState, request: Request) -> OnboardingState:
    path = _state_path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".user-state-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump({"onboarding_completed_version": state.completed_version}, stream)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return state
