from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope


# Windows MIME registrations can label .mjs as text/plain. Browser modules
# require a JavaScript type regardless of programs installed on the host.
WEB_MEDIA_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".wasm": "application/wasm",
}


class WebStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        media_type = WEB_MEDIA_TYPES.get(Path(path).suffix.lower())
        if media_type and response.status_code == 200:
            response.headers["content-type"] = media_type
        return response


def mount_web_app(application: FastAPI, web_dir: Path) -> None:
    index_path = web_dir / "index.html"
    assets_dir = web_dir / "assets"
    if not index_path.is_file():
        raise RuntimeError(f"前端资源不完整，缺少：{index_path}")
    if assets_dir.is_dir():
        application.mount("/assets", WebStaticFiles(directory=assets_dir), name="web-assets")

    @application.get("/{web_path:path}", include_in_schema=False)
    def serve_web_app(web_path: str) -> FileResponse:
        if web_path == "api" or web_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (web_dir / web_path).resolve()
        try:
            candidate.relative_to(web_dir.resolve())
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Not Found") from error
        if web_path and candidate.is_file():
            return FileResponse(candidate, media_type=WEB_MEDIA_TYPES.get(candidate.suffix.lower()))
        return FileResponse(index_path)
