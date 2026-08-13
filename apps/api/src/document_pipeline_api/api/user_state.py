import json
import os
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/user-state", tags=["user-state"])


class OnboardingState(BaseModel):
    completed_version: int = 0


def _state_path(request: Request) -> Path:
    return request.app.state.settings.storage_dir.parent / "user-state.json"


@router.get("/onboarding", response_model=OnboardingState)
def get_onboarding(request: Request) -> OnboardingState:
    try:
        payload = json.loads(_state_path(request).read_text(encoding="utf-8"))
        version = payload.get("onboarding_completed_version", 0)
        return OnboardingState(completed_version=version if isinstance(version, int) else 0)
    except (OSError, TypeError, json.JSONDecodeError):
        return OnboardingState(completed_version=0)


@router.put("/onboarding", response_model=OnboardingState)
def update_onboarding(state: OnboardingState, request: Request) -> OnboardingState:
    path = _state_path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"onboarding_completed_version": state.completed_version}), encoding="utf-8"
    )
    os.replace(temporary, path)
    return state
