from dataclasses import dataclass
import os
from pathlib import Path

from document_pipeline_api.runtime_paths import default_data_dir


@dataclass(frozen=True)
class Settings:
    database_url: str
    storage_dir: Path
    max_upload_bytes: int = 50 * 1024 * 1024
    max_request_bytes: int = 55 * 1024 * 1024
    max_filename_chars: int = 255
    max_image_total_pixels: int = 100_000_000
    max_import_bytes: int = 25 * 1024 * 1024
    max_import_uncompressed_bytes: int = 256 * 1024 * 1024
    max_import_rows: int = 100_000
    max_import_columns: int = 512
    max_template_sample_files: int = 5
    max_template_sample_total_bytes: int = 50 * 1024 * 1024
    model_provider: str = "lm_studio"
    model_base_url: str = "http://127.0.0.1:1234/v1"
    model_name: str = "qwen3.5-4b"
    model_api_key: str = ""
    model_reasoning_effort: str | None = "none"
    model_timeout_seconds: float = 180
    model_context_length: int = 8192
    model_temperature: float | None = None
    max_pdf_pages: int = 10
    max_active_tasks: int = 100
    max_active_bytes: int = 512 * 1024 * 1024
    max_active_pages: int = 200
    queue_enabled: bool = False
    recover_local_model_on_startup: bool = False
    integration_read_token: str = ""
    integration_write_token: str = ""
    development_origins_enabled: bool = False

    @classmethod
    def local(cls) -> "Settings":
        data_dir = default_data_dir()
        return cls(
            database_url=f"sqlite:///{data_dir / 'document-pipeline.db'}",
            storage_dir=data_dir / "uploads",
            model_provider=os.getenv("DOCUMENT_PIPELINE_MODEL_PROVIDER", "openai_compatible"),
            model_base_url=os.getenv(
                "DOCUMENT_PIPELINE_MODEL_BASE_URL",
                "http://127.0.0.1:1234/v1",
            ),
            model_name=os.getenv("DOCUMENT_PIPELINE_MODEL_NAME", "qwen3.5-4b"),
            max_request_bytes=int(
                os.getenv("DOCUMENT_PIPELINE_MAX_REQUEST_BYTES", str(55 * 1024 * 1024))
            ),
            max_filename_chars=int(
                os.getenv("DOCUMENT_PIPELINE_MAX_FILENAME_CHARS", "255")
            ),
            max_image_total_pixels=int(
                os.getenv("DOCUMENT_PIPELINE_MAX_IMAGE_TOTAL_PIXELS", "100000000")
            ),
            max_import_bytes=int(
                os.getenv("DOCUMENT_PIPELINE_MAX_IMPORT_BYTES", str(25 * 1024 * 1024))
            ),
            max_import_uncompressed_bytes=int(
                os.getenv(
                    "DOCUMENT_PIPELINE_MAX_IMPORT_UNCOMPRESSED_BYTES",
                    str(256 * 1024 * 1024),
                )
            ),
            max_import_rows=int(
                os.getenv("DOCUMENT_PIPELINE_MAX_IMPORT_ROWS", "100000")
            ),
            max_import_columns=int(
                os.getenv("DOCUMENT_PIPELINE_MAX_IMPORT_COLUMNS", "512")
            ),
            max_template_sample_files=int(
                os.getenv("DOCUMENT_PIPELINE_MAX_TEMPLATE_SAMPLE_FILES", "5")
            ),
            max_template_sample_total_bytes=int(
                os.getenv(
                    "DOCUMENT_PIPELINE_MAX_TEMPLATE_SAMPLE_TOTAL_BYTES",
                    str(50 * 1024 * 1024),
                )
            ),
            model_api_key=os.getenv("DOCUMENT_PIPELINE_MODEL_API_KEY", ""),
            model_reasoning_effort=(
                os.getenv("DOCUMENT_PIPELINE_MODEL_REASONING_EFFORT", "none") or None
            ),
            model_timeout_seconds=float(
                os.getenv("DOCUMENT_PIPELINE_MODEL_TIMEOUT_SECONDS", "180")
            ),
            model_context_length=int(
                os.getenv("DOCUMENT_PIPELINE_MODEL_CONTEXT_LENGTH", "8192")
            ),
            model_temperature=(
                float(os.getenv("DOCUMENT_PIPELINE_MODEL_TEMPERATURE", "0.1"))
                if os.getenv("DOCUMENT_PIPELINE_MODEL_TEMPERATURE")
                else None
            ),
            max_pdf_pages=int(os.getenv("DOCUMENT_PIPELINE_MAX_PDF_PAGES", "10")),
            max_active_tasks=int(
                os.getenv("DOCUMENT_PIPELINE_MAX_ACTIVE_TASKS", "100")
            ),
            max_active_bytes=int(
                os.getenv(
                    "DOCUMENT_PIPELINE_MAX_ACTIVE_BYTES",
                    str(512 * 1024 * 1024),
                )
            ),
            max_active_pages=int(
                os.getenv("DOCUMENT_PIPELINE_MAX_ACTIVE_PAGES", "200")
            ),
            queue_enabled=True,
            recover_local_model_on_startup=True,
            integration_read_token=os.getenv(
                "DOCUMENT_PIPELINE_INTEGRATION_READ_TOKEN",
                "",
            ),
            integration_write_token=os.getenv(
                "DOCUMENT_PIPELINE_INTEGRATION_WRITE_TOKEN",
                "",
            ),
            development_origins_enabled=(
                os.getenv("DOCUMENT_PIPELINE_DEVELOPMENT_ORIGINS", "0") == "1"
            ),
        )
