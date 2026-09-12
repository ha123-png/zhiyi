"""Serve only an explicitly supplied synthetic verification directory; no worker."""
from argparse import ArgumentParser
from pathlib import Path

import uvicorn

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app

if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    project = Path(__file__).resolve().parents[1]
    if not root.is_relative_to(project / ".local"):
        parser.error("Verification data must be inside this workspace's .local directory.")
    settings = Settings(database_url=f"sqlite:///{root / 'document-pipeline.db'}", storage_dir=root / "uploads", queue_enabled=False)
    uvicorn.run(create_app(settings, web_dir=project / "apps/web/dist"), host="127.0.0.1", port=args.port)
