"""Bind manual assistant verification to its own synthetic SQLite instance."""

import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy.orm import Session

from document_pipeline_api.models import DataTableRecord


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _checked_local_path(path: Path, boundary: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    resolved = absolute.resolve()
    if (resolved == boundary or not resolved.is_relative_to(boundary) or
            not absolute.is_relative_to(boundary)):
        raise ValueError("Synthetic data and reports must stay inside workspace .local")
    current = absolute
    while current != boundary and current.is_relative_to(boundary):
        if _linked(current):
            raise ValueError("Synthetic data and report paths must not traverse links")
        current = current.parent
    return resolved


def validate_paths(project: Path, data: Path, output: Path) -> tuple[Path, Path]:
    if _linked(project / ".local"):
        raise ValueError("Workspace .local must not be a link")
    boundary = (project / ".local").resolve()
    data_path = _checked_local_path(data, boundary)
    output_path = _checked_local_path(output, boundary)
    database = data_path / "document-pipeline.db"
    if _linked(database) or not database.is_file():
        raise ValueError("The isolated synthetic database is missing or linked")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = database.with_name(database.name + suffix)
        if _linked(sidecar) or (sidecar.exists() and not sidecar.is_file()):
            raise ValueError("Synthetic database sidecars must not be links or directories")
    if output_path.exists() or _linked(output_path):
        raise FileExistsError("The report already exists; preserve it and choose a new path")
    return data_path, output_path


def validate_loopback_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        address = ipaddress.ip_address(parts.hostname or "")
        port = parts.port
    except ValueError as error:
        raise ValueError("Acceptance URL must use a loopback IP and explicit port") from error
    if (parts.scheme != "http" or not address.is_loopback or port is None or not 1 <= port <= 65535 or
            parts.username is not None or parts.password is not None or
            parts.path not in {"", "/"} or parts.query or parts.fragment):
        raise ValueError("Acceptance URL must be plain HTTP on a loopback IP with no path or credentials")
    return value.rstrip("/")


def reserve_report(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write('{"status":"checking_server_identity"}\n')


def require_bound_server(engine, client) -> None:
    """Use a unique DB-only challenge to identify the server without a new endpoint.

    The ID and its unrelated random name are generated per call.  Only the
    selected synthetic database can answer with both values.  The challenge is
    removed on success and failure without touching any pre-existing table.
    """
    challenge_id = str(uuid4())
    challenge_name = f"synthetic-verification-{uuid4().hex}"
    template_key = f"manual:verification-{challenge_id}"
    inserted = False
    try:
        with Session(engine) as session:
            if session.get(DataTableRecord, "ask-synthetic") is None:
                raise ValueError("The selected database lacks the synthetic verification marker")
            session.add(DataTableRecord(
                id=challenge_id, name=challenge_name, template_key=template_key,
                template_version="1", document_kind="custom",
                columns_json=json.dumps([{"key": "probe", "label": "probe", "section": "item", "value_type": "text"}]),
            ))
            session.commit()
            inserted = True
        response = client.get(f"/tables/{challenge_id}", headers={"Cache-Control": "no-cache"}, follow_redirects=False)
        if response.status_code != 200 or getattr(response, "history", []):
            raise ValueError("The acceptance server does not identify the selected synthetic database")
        value = response.json()
        if (not isinstance(value, dict) or value.get("id") != challenge_id or
                value.get("name") != challenge_name or value.get("template_key") != template_key):
            raise ValueError("The acceptance server does not identify the selected synthetic database")
    finally:
        if inserted:
            with Session(engine) as session:
                challenge = session.get(DataTableRecord, challenge_id)
                if (challenge is None or challenge.name != challenge_name or
                        challenge.template_key != template_key):
                    raise RuntimeError("The owned synthetic identity challenge changed before cleanup")
                session.delete(challenge)
                session.commit()
