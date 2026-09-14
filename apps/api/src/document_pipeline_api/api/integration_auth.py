from dataclasses import dataclass
from secrets import compare_digest
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from document_pipeline_api.config import Settings
from document_pipeline_api.services.integration_config import read_integration_config


@dataclass(frozen=True)
class IntegrationPrincipal:
    scope: Literal["read", "write"]


bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def validate_integration_tokens(settings: Settings) -> None:
    configured = [
        token
        for token in (
            settings.integration_read_token,
            settings.integration_write_token,
        )
        if token
    ]
    if any(len(token) < 32 for token in configured):
        raise RuntimeError("外部集成密钥至少需要 32 个字符。")
    if (
        settings.integration_read_token
        and settings.integration_read_token == settings.integration_write_token
    ):
        raise RuntimeError("读取密钥和写入密钥不能相同。")


def require_integration_read(
    request: Request,
    credentials: Credentials,
) -> IntegrationPrincipal:
    settings: Settings = request.app.state.settings
    config = read_integration_config(settings.storage_dir.parent)
    from document_pipeline_api.services.integration_config import integration_config_path
    managed = getattr(request.app.state, "integration_config_managed", False) or integration_config_path(settings.storage_dir.parent).exists()
    request.app.state.integration_config_managed = managed
    # Environment credentials are supported only for never-managed headless use.
    # Once managed, a missing/revoked credential must never revive a startup copy.
    read_token = config.get("read_token", "" if managed else settings.integration_read_token)
    write_token = config.get("write_token", "" if managed else settings.integration_write_token)
    if not read_token and not write_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="外部集成接口尚未启用。",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _invalid_credentials()

    token = credentials.credentials
    if write_token and compare_digest(
        token,
        write_token,
    ):
        return IntegrationPrincipal(scope="write")
    if read_token and compare_digest(
        token,
        read_token,
    ):
        return IntegrationPrincipal(scope="read")
    raise _invalid_credentials()


def require_integration_write(
    principal: Annotated[IntegrationPrincipal, Depends(require_integration_read)],
) -> IntegrationPrincipal:
    if principal.scope != "write":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="这个密钥只有读取权限。",
        )
    return principal


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="集成密钥无效。",
        headers={"WWW-Authenticate": "Bearer"},
    )
