from dataclasses import dataclass
import os
from secrets import compare_digest
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from document_pipeline_api.config import Settings
from document_pipeline_api.services.integration_config import is_current_http_key, read_integration_config


@dataclass(frozen=True)
class IntegrationPrincipal:
    scope: Literal["read", "write"]
    revision_read: bool = False
    original_read: bool = False
    table_ids: tuple[str, ...] | None = None


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
    if any(len(token) < 32 or not token.isascii() or not token.isprintable()
           or any(char.isspace() for char in token) for token in configured):
        raise RuntimeError("外部集成密钥至少需要 32 个非空白可打印 ASCII 字符。")
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
    # HTTP headers may contain latin-1 bytes. compare_digest(str, str) only
    # accepts ASCII: malformed credentials must remain a 401, never a 500.
    if not token.isascii():
        raise _invalid_credentials()
    if write_token and write_token.isascii() and compare_digest(
        token,
        write_token,
    ):
        return _principal(config, "write", token)
    if read_token and read_token.isascii() and compare_digest(
        token,
        read_token,
    ):
        return _principal(config, "read", token)
    raise _invalid_credentials()


def _principal(config: dict[str, str], kind: Literal["read", "write"], token: str) -> IntegrationPrincipal:
    if is_current_http_key(token):
        return IntegrationPrincipal(scope=kind, revision_read=True, original_read=True)
    # Keep access that was granted to credentials already issued by earlier builds.
    table_ids = tuple(filter(None, config.get(f"api_{kind}_table_ids", "").split(os.pathsep)))
    return IntegrationPrincipal(
        scope=kind,
        revision_read=bool(table_ids) and config.get(f"api_{kind}_revision_read") == "1",
        original_read=bool(table_ids) and config.get(f"api_{kind}_original_read") == "1",
        table_ids=table_ids,
    )


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
