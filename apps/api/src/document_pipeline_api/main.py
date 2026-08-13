from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from document_pipeline_api.api.admin import router as admin_router
from document_pipeline_api.api.backups import router as backups_router
from document_pipeline_api.api.data_tables import router as data_tables_router
from document_pipeline_api.api.events import router as events_router
from document_pipeline_api.api.health import router as health_router
from document_pipeline_api.api.integration import router as integration_router
from document_pipeline_api.api.integration_auth import validate_integration_tokens
from document_pipeline_api.api.integration_config import (
    router as integration_config_router,
)
from document_pipeline_api.api.models import router as models_router
from document_pipeline_api.api.queue_control import router as queue_control_router
from document_pipeline_api.api.stats import router as stats_router
from document_pipeline_api.api.system import router as system_router
from document_pipeline_api.api.tasks import router as tasks_router
from document_pipeline_api.api.templates import router as templates_router
from document_pipeline_api.api.user_state import router as user_state_router
from document_pipeline_api.config import Settings
from document_pipeline_api.db import build_engine
from document_pipeline_api.migrations import upgrade_database
from document_pipeline_api.model_secrets import ModelSecretStore
from document_pipeline_api.models import TaskRecord  # noqa: F401
from document_pipeline_api.local_http_security import enforce_local_browser_origin
from document_pipeline_api.runtime_paths import bundled_web_dir
from document_pipeline_api.request_limits import RequestSizeLimitMiddleware
from document_pipeline_api.version import __version__
from document_pipeline_api.web_app import mount_web_app


async def _database_operational_error(
    _request: Request,
    error: OperationalError,
) -> JSONResponse:
    message = str(error.orig).lower()
    if "database is locked" in message or "database is busy" in message:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "数据库正在处理其他写入，本次操作未完成，请稍后重试。"},
            headers={"Retry-After": "1"},
        )
    if "database or disk is full" in message or "readonly database" in message:
        return JSONResponse(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            content={"detail": "数据库无法写入，请检查磁盘空间和数据目录权限后重试。"},
        )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "数据库操作失败，未完成的更改已回滚。"},
    )


async def _privacy_safe_validation_error(
    _request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    details = [
        {
            "type": item.get("type", "validation_error"),
            "loc": item.get("loc", ()),
            "msg": item.get("msg", "请求内容不符合要求。"),
        }
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": details},
    )


def create_app(
    settings: Settings | None = None,
    *,
    web_dir: Path | None = None,
    model_secret_store: ModelSecretStore | None = None,
) -> FastAPI:
    active_settings = settings or Settings.local()
    engine = build_engine(active_settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        validate_integration_tokens(active_settings)
        active_settings.storage_dir.mkdir(parents=True, exist_ok=True)
        upgrade_database(engine)
        from document_pipeline_api.services.templates import ensure_builtin_templates
        from document_pipeline_api.services.model_profiles import (
            ensure_default_local_profile,
            leave_experimental_one_click_profile,
        )

        with application.state.session_factory() as session:
            ensure_builtin_templates(session)
            leave_experimental_one_click_profile(session)
            ensure_default_local_profile(session)
        if active_settings.queue_enabled:
            from document_pipeline_api.services.queueing import recover_missing_queued_tasks
            from document_pipeline_api.services.task_leases import (
                recover_expired_task_leases,
            )

            with application.state.session_factory() as session:
                recover_expired_task_leases(session)
                recover_missing_queued_tasks(session)
            if active_settings.recover_local_model_on_startup:
                from document_pipeline_api.services.model_setup import (
                    start_runtime_recovery,
                )

                start_runtime_recovery(application.state.session_factory, active_settings)
        try:
            yield
        finally:
            engine.dispose()
            if active_settings.queue_enabled:
                from document_pipeline_api.queue import huey

                huey.storage.close()

    application = FastAPI(
        title="知意 API",
        description="Local-first multimodal document data pipeline.",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = active_settings
    application.state.model_secret_store = model_secret_store
    application.state.session_factory = sessionmaker(engine, expire_on_commit=False)
    application.add_middleware(
        RequestSizeLimitMiddleware,
        max_bytes=active_settings.max_request_bytes,
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    application.middleware("http")(enforce_local_browser_origin)
    application.add_exception_handler(RequestValidationError, _privacy_safe_validation_error)
    application.add_exception_handler(OperationalError, _database_operational_error)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=(
            [
                "http://127.0.0.1:5173",
                "http://localhost:5173",
                "http://127.0.0.1:5180",
                "http://localhost:5180",
            ]
            if active_settings.development_origins_enabled
            else []
        ),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(health_router, prefix="/api/v1")
    application.include_router(admin_router, prefix="/api/v1")
    application.include_router(backups_router, prefix="/api/v1")
    application.include_router(integration_router, prefix="/api")
    application.include_router(integration_config_router, prefix="/api/v1")
    application.include_router(data_tables_router, prefix="/api/v1")
    application.include_router(events_router, prefix="/api/v1")
    application.include_router(models_router, prefix="/api/v1")
    application.include_router(queue_control_router, prefix="/api/v1")
    application.include_router(stats_router, prefix="/api/v1")
    application.include_router(system_router, prefix="/api/v1")
    application.include_router(tasks_router, prefix="/api/v1")
    application.include_router(templates_router, prefix="/api/v1")
    application.include_router(user_state_router, prefix="/api/v1")
    active_web_dir = web_dir if web_dir is not None else bundled_web_dir()
    if active_web_dir is not None:
        mount_web_app(application, active_web_dir)
    return application


app = create_app()
