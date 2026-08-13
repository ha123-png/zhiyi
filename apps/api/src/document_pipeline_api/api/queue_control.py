from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from document_pipeline_api.db import get_session
from document_pipeline_api.services.queue_pause import (
    is_queue_paused,
    queue_pause,
    queue_resume,
)

router = APIRouter(prefix="/queue", tags=["queue"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/status")
def status(session: SessionDependency) -> dict[str, bool]:
    """队列是否处于暂停状态（冻结整个队列）。"""
    return {"paused": is_queue_paused(session)}


@router.post("/pause")
def pause(request: Request, session: SessionDependency) -> dict[str, bool]:
    """暂停整个队列：中断当前处理中的任务，冻结所有排队任务。"""
    queue_pause(session, request.app.state.settings)
    return {"paused": True}


@router.post("/resume")
def resume(request: Request, session: SessionDependency) -> dict[str, bool]:
    """恢复整个队列：把所有暂停任务放回队列继续处理。"""
    queue_resume(session, request.app.state.settings)
    return {"paused": False}
