from fastapi import APIRouter

from document_pipeline_api.services.handbook import handbook

router = APIRouter(prefix="/guide", tags=["guide"])


@router.get("")
def guide_index():
    return handbook()


@router.get("/{topic}")
def guide_topic(topic: str):
    return handbook(topic)
