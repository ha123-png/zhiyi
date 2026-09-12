from __future__ import annotations

from collections.abc import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyTooLarge(Exception):
    pass


class RequestSizeLimitMiddleware:
    """在 multipart 解析和临时文件落盘前限制完整 HTTP 请求体。"""

    def __init__(self, app: ASGIApp, *, max_bytes: int, upload_limit: Callable[[], int | None] | None = None) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.upload_limit = upload_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_bytes = self.max_bytes
        if self.upload_limit and scope.get("path") == "/api/v1/tasks" and scope.get("method") == "POST":
            configured = self.upload_limit()
            if configured is not None:
                max_bytes = configured + 1024 * 1024  # multipart overhead
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                if int(raw_length) > max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass

        consumed = 0

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > max_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": "请求内容超过当前允许的大小。"},
        )
        await response(scope, receive, send)
