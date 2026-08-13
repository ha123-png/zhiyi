from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
DEVELOPMENT_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5180",
    "http://localhost:5180",
}


async def enforce_local_browser_origin(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    origin = request.headers.get("origin")
    if request.method in UNSAFE_METHODS and origin and not _allowed_origin(request, origin):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "已拒绝来自其他网页的本机写入请求。"},
        )
    return await call_next(request)


def _allowed_origin(request: Request, origin: str) -> bool:
    settings = getattr(request.app.state, "settings", None)
    if origin in DEVELOPMENT_ORIGINS and bool(
        getattr(settings, "development_origins_enabled", False)
    ):
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return False
    host = request.headers.get("host", "")
    return parsed.netloc == host and parsed.hostname in {"127.0.0.1", "localhost"}
