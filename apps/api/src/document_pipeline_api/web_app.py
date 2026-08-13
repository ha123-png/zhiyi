from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount_web_app(application: FastAPI, web_dir: Path) -> None:
    index_path = web_dir / "index.html"
    assets_dir = web_dir / "assets"
    if not index_path.is_file():
        raise RuntimeError(f"前端资源不完整，缺少：{index_path}")
    if assets_dir.is_dir():
        application.mount("/assets", StaticFiles(directory=assets_dir), name="web-assets")

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
            return FileResponse(candidate)
        return FileResponse(index_path)
