"""Bind a local candidate to its source inputs and complete onedir payload."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_record(root: Path, path: Path) -> dict:
    if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root):
        raise ValueError("Release inputs must not contain links or paths outside the root")
    return {"path": path.relative_to(root).as_posix(), "size_bytes": path.stat().st_size,
            "sha256": digest(path)}


def source_snapshot(root: Path) -> dict:
    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(root), *args])

    names = sorted(set(git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
                       .decode("utf-8").rstrip("\0").split("\0")) - {""})
    records = []
    for name in names:
        path = root / name
        if path.is_file():
            records.append(file_record(root, path))
        elif not path.exists():
            records.append({"path": name, "missing": True})
        else:
            raise ValueError("Unexpected non-file source input")
    return {"schema_version": 1, "git_revision": git("rev-parse", "HEAD").decode().strip(),
            "source_dirty": bool(git("status", "--porcelain", "-z")), "files": records}


def payload_snapshot(root: Path) -> dict:
    # Reject directory links as well as file links before traversing them.
    paths = sorted(root.rglob("*"))
    for path in paths:
        if path.is_symlink() or path.is_junction():
            raise ValueError("Release payload must not contain links")
    records = [file_record(root, path) for path in paths if path.is_file()]
    if not records:
        raise ValueError("Release payload is empty")
    return {"schema_version": 1, "scope": "Every file under the onedir payload root",
            "file_count": len(records), "files": records}


def verify_source(root: Path, saved: dict) -> None:
    if source_snapshot(root) != saved:
        raise ValueError("Source inputs changed during the build; candidate is invalid")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("source", "verify-source", "payload"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if args.operation == "verify-source":
        verify_source(root, json.loads(args.manifest.read_text(encoding="utf-8")))
        print("Source inputs unchanged.")
        return
    if args.operation == "payload" and args.manifest.resolve().is_relative_to(root):
        parser.error("Payload manifest must be outside the payload to avoid self-reference")
    result = source_snapshot(root) if args.operation == "source" else payload_snapshot(root)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with args.manifest.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"Recorded {len(result['files'])} files.")


if __name__ == "__main__":
    main()
