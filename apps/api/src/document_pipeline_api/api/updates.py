from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Annotated
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request
import httpx
from pydantic import BaseModel
from sqlalchemy.orm import Session

from document_pipeline_api.db import get_session
from document_pipeline_api.services.read_snapshot import begin_write_snapshot
from document_pipeline_api.services.system_settings import get_bool_setting, set_bool_setting
from document_pipeline_api.version import __version__


router = APIRouter(prefix="/updates", tags=["updates"])
SessionDependency = Annotated[Session, Depends(get_session)]
RELEASE_PAGE = "https://github.com/ha123-png/zhiyi/releases"
RELEASE_API = "https://api.github.com/repos/ha123-png/zhiyi/releases"
SETTING_KEY = "auto_check_updates"
_LOCK = Lock()
_last: dict | None = None
_DOWNLOAD_LOCK = Lock()
MAX_INSTALLER_BYTES = 1024 * 1024 * 1024


class UpdateSettings(BaseModel):
    auto_check: bool = False


def _version(tag: str):
    match = re.fullmatch(r"v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})(?:\.(\d{1,6}))?", tag)
    return tuple(int(value or "0") for value in match.groups()) if match else None


def _fetch_releases():
    with httpx.Client(timeout=7, follow_redirects=False) as client:
        response = client.get(RELEASE_API, params={"per_page": 50},
                              headers={"Accept": "application/vnd.github+json"})
        response.raise_for_status()
        return response.json()


def _check():
    releases = _fetch_releases()
    if not isinstance(releases, list):
        raise ValueError("Invalid release list")
    candidates = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") is not False:
            continue
        tag = release.get("tag_name")
        version = _version(tag) if isinstance(tag, str) else None
        if version is None:
            continue
        number = tag.removeprefix("v")
        assets = release.get("assets")
        if not isinstance(assets, list):
            continue
        asset = next((asset for asset in assets if
            isinstance(asset, dict) and asset.get("name") == f"Zhiyi-{number}-win-x64-setup.exe"
            and asset.get("state") == "uploaded" for asset in assets
        ), None)
        if asset is None:
            continue
        candidates.append((version, number, tag, asset))
    if not candidates:
        raise ValueError("No published Windows release")
    version, number, tag, asset = max(candidates, key=lambda item: item[:3])
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "available": version > _version(__version__),
        "latest_version": number,
        "release_url": f"{RELEASE_PAGE}/tag/{tag}",
        "downloadable": _asset_digest(asset) is not None,
        "_asset": asset,
    }


def _asset_digest(asset):
    digest = asset.get("digest")
    size = asset.get("size")
    if isinstance(digest, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", digest) and type(size) is int and 0 < size <= MAX_INSTALLER_BYTES:
        return digest[7:]
    return None


def _public(check):
    return {key: value for key, value in check.items() if key != "_asset"} if check else None


@router.get("")
def update_status(session: SessionDependency, request: Request):
    data_dir = request.app.state.settings.storage_dir.parent
    return {"current_version": __version__, "auto_check": get_bool_setting(session, SETTING_KEY, default=False),
            "result": _public(_last), "downloaded_version": downloaded_version(data_dir),
            "install_failed": _installation_failed(data_dir)}


def _installation_failed(data_dir: Path) -> bool:
    try:
        return (download_directory(data_dir) / "install-failed").is_file()
    except ValueError:
        return False


@router.put("")
def save_update_settings(body: UpdateSettings, session: SessionDependency):
    begin_write_snapshot(session)
    set_bool_setting(session, SETTING_KEY, body.auto_check)
    session.commit()
    return body


@router.post("/check")
def check_update():
    global _last
    with _LOCK:
        if _last and datetime.now(timezone.utc) - datetime.fromisoformat(_last["checked_at"]) < timedelta(minutes=2):
            return _public(_last)
        try:
            _last = _check()
        except (ValueError, httpx.HTTPError) as error:
            raise HTTPException(503, "暂时无法检查更新。可稍后重试，或前往发行页查看。") from error
        return _public(_last)


def download_directory(data_dir: Path) -> Path:
    directory = data_dir / "runtime" / "app-updates"
    for path in (directory.parent, directory):
        if path.is_symlink() or path.is_junction():
            raise ValueError("更新缓存目录无效。")
    return directory


def downloaded_version(data_dir: Path) -> str | None:
    try:
        directory = download_directory(data_dir)
        if not (directory / "update.exe").is_file():
            return None
        manifest = json.loads((directory / "download.json").read_text(encoding="utf-8"))
        version = _version(manifest["version"])
        if version is not None and version > _version(__version__):
            return manifest["version"]
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def _asset_client() -> httpx.Client:
    return httpx.Client(timeout=30, follow_redirects=True, max_redirects=5, event_hooks={"request": [_check_asset_host]})


@router.post("/download")
def download_update(request: Request):
    if not _DOWNLOAD_LOCK.acquire(blocking=False):
        raise HTTPException(409, "正在下载更新，请稍候。")
    temporary = None
    try:
        check_update()
        check = _last
        asset = check["_asset"]
        digest = _asset_digest(asset)
        if not check["available"] or digest is None:
            raise HTTPException(409, "没有可校验的新版安装包。")
        profile = request.app.state.settings.storage_dir.parent
        directory = download_directory(profile)
        directory.mkdir(parents=True, exist_ok=True)
        url = check["release_url"].replace("/tag/", "/download/") + "/" + asset["name"]
        size = 0
        checksum = hashlib.sha256()
        # Start from the fixed official release URL. GitHub handles the asset redirect.
        with _asset_client() as client, client.stream("GET", url) as response:
            response.raise_for_status()
            with NamedTemporaryFile(dir=directory, prefix="download-", suffix=".part", delete=False) as handle:
                temporary = Path(handle.name)
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > asset["size"]:
                        raise ValueError("Unexpected update size")
                    checksum.update(chunk)
                    handle.write(chunk)
        if size != asset["size"] or checksum.hexdigest() != digest:
            raise ValueError("Update checksum mismatch")
        temporary.replace(directory / "update.exe")
        from document_pipeline_api.services.clear_data import _write_json
        _write_json(directory / "download.json", {"version": check["latest_version"], "sha256": digest})
        (directory / "install-failed").unlink(missing_ok=True)
        return {"downloaded_version": check["latest_version"]}
    except (OSError, ValueError, httpx.HTTPError) as error:
        raise HTTPException(503, "更新下载或校验失败。请重试。") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        _DOWNLOAD_LOCK.release()


def _check_asset_host(request: httpx.Request) -> None:
    if request.url.scheme != "https" or request.url.host not in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}:
        raise ValueError("Unexpected update asset host")
