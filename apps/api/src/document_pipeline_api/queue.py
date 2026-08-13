import os
from pathlib import Path

from huey import SqliteHuey


queue_path = Path(os.getenv("DOCUMENT_PIPELINE_QUEUE_DB", "data/queue.db"))
queue_path.parent.mkdir(parents=True, exist_ok=True)
huey = SqliteHuey(
    "document-pipeline",
    filename=str(queue_path),
    results=True,
    store_none=True,
)
