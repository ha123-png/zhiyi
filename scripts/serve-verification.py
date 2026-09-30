"""Serve only an explicitly supplied synthetic verification directory; no worker."""
from argparse import ArgumentParser
from pathlib import Path

import uvicorn

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.model_secrets import ModelSecretStore, SecretNotFoundError


class MemoryCredentialBackend:
    """Synthetic test credentials only, discarded when the isolated server exits."""

    def __init__(self):
        self.values = {}

    def write(self, target, secret):
        self.values[target] = secret

    def read(self, target):
        try:
            return self.values[target]
        except KeyError as error:
            raise SecretNotFoundError("missing") from error

    def delete(self, target):
        if target not in self.values:
            raise SecretNotFoundError("missing")
        del self.values[target]

if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--web-dir", type=Path, help="Optional frozen build inside .local; avoids parallel build replacement.")
    parser.add_argument("--memory-credentials", action="store_true", help="Use ephemeral synthetic credentials, never the OS store.")
    args = parser.parse_args()
    root = args.data_dir.resolve()
    project = Path(__file__).resolve().parents[1]
    if not root.is_relative_to(project / ".local"):
        parser.error("Verification data must be inside this workspace's .local directory.")
    web_dir = args.web_dir.resolve() if args.web_dir else project / "apps/web/dist"
    if args.web_dir and not web_dir.is_relative_to(project / ".local"):
        parser.error("A frozen verification build must be inside this workspace's .local directory.")
    if not (web_dir / "index.html").is_file():
        parser.error("Build the frontend before starting verification.")
    settings = Settings(database_url=f"sqlite:///{root / 'document-pipeline.db'}", storage_dir=root / "uploads", queue_enabled=False)
    secret_store = ModelSecretStore(MemoryCredentialBackend()) if args.memory_credentials else None
    uvicorn.run(create_app(settings, web_dir=web_dir, model_secret_store=secret_store), host="127.0.0.1", port=args.port)
