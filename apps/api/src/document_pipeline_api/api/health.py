from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from document_pipeline_api.version import __version__


router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="document-pipeline-api",
        version=__version__,
    )
