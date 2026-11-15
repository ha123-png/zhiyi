"""Check current documentation entries and links without rewriting historical files."""

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import unquote


def normalized_path(root: Path, value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return str(path.resolve()).casefold().rstrip("\\/")


def zip_member(value: str) -> str:
    raw = str(value).replace("\\", "/")
    if not raw:
        return ""
    member = PurePosixPath(raw)
    if ".." in member.parts:
        return ""
    result = member.as_posix().lstrip("/")
    return "" if result == "." else result


def path_is_same_or_child(path: str, parent: str) -> bool:
    normalized_parent = parent.rstrip("\\/")
    return (
        path == normalized_parent
        or path.startswith(normalized_parent + "\\")
        or path.startswith(normalized_parent + "/")
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_product_catalog(root: Path, catalog: dict) -> dict:
    """Validate an explicit public document set without private history ledgers."""
    root = root.resolve()
    invalid = []
    private = []
    paths = []
    for entry in catalog.get("entries", []):
        name = entry.get("path") if isinstance(entry, dict) else None
        if isinstance(name, str) and name:
            paths.append(name)
        else:
            invalid.append({"path": name, "reason": "catalog entry needs a nonempty path"})
    duplicates = sorted(name for name, count in Counter(paths).items() if count > 1)

    def product_path(name: str):
        normalized = name.replace("\\", "/")
        relative = PurePosixPath(normalized)
        if relative.is_absolute() or ".." in relative.parts or re.match(r"^[A-Za-z]:", normalized):
            return None
        if any(part.casefold() in {".local", ".git", ".venv", "archive", "workspace-governance", "private", "keys"}
               for part in relative.parts) or normalized in {"AGENTS.md", "工作区导航.html"}:
            return None
        if relative.suffix.casefold() in {".key", ".pem", ".pfx", ".p12", ".db", ".dpbak"} or (
            relative.name.startswith(".env") and relative.name != ".env.example"
        ):
            return None
        target = (root / normalized).resolve()
        return target if target.is_relative_to(root) else None

    declared = {}
    missing = []
    for name in paths:
        target = product_path(name)
        if target is None:
            invalid.append({"path": name, "reason": "product document path must stay in the public tree"})
        else:
            declared[target] = name
            if not target.is_file():
                missing.append(name)
    current = catalog.get("current_docs")
    if not isinstance(current, list) or not current or not all(isinstance(name, str) and name for name in current):
        invalid.append({"path": "current_docs", "reason": "product catalog needs an explicit nonempty current_docs list"})
        current = []
    authority = catalog.get("authoritative_current_document")
    if authority not in current:
        invalid.append({"path": authority, "reason": "authoritative document must be included in current_docs"})
    broken = []
    checked = 0
    for name in sorted(set(current)):
        document = product_path(name)
        if document not in declared:
            invalid.append({"path": name, "reason": "current document is absent from the catalog"})
            continue
        if not document.is_file():
            continue
        checked += 1
        text = document.read_text(encoding="utf-8")
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
        links += re.findall(r"<(?:a|img)\b[^>]*?\b(?:href|src)=[\"']([^\"']+)[\"']", text, re.IGNORECASE)
        for raw in links:
            target = unquote(raw.strip("<>").split("#", 1)[0])
            if not target or target.casefold().startswith(("https://", "http://", "mailto:")):
                continue
            if "://" in target or re.match(r"^[A-Za-z]:", target) or target.startswith(("/", "\\")):
                private.append({"file": name, "target": target})
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.is_relative_to(root) or product_path(resolved.relative_to(root).as_posix()) is None:
                private.append({"file": name, "target": target})
            elif not resolved.exists() or (resolved.suffix.casefold() == ".md" and resolved not in declared):
                broken.append({"file": name, "target": target})
    return {
        "publication_scope": "product",
        "status_entry_count": len(paths),
        "current_documents_checked": checked,
        "missing_documents": sorted(set(missing)),
        "duplicate_documents": duplicates,
        "invalid_catalog_paths": invalid,
        "broken_current_links": broken,
        "private_or_external_file_links": private,
        "private_history_ledgers_checked": False,
        "product_tests_run": False,
        "scope": "Explicit product catalog/current_docs and their local product links only; no private historical acceptance is implied.",
        "passed": not any((missing, duplicates, invalid, broken, private)),
    }


def check(root: Path) -> dict:
    catalog = json.loads((root / "docs/document-status.json").read_text(encoding="utf-8"))
    if catalog.get("publication_scope") == "product":
        return check_product_catalog(root, catalog)
    paths = [entry["path"] for entry in catalog["entries"]]
    missing = [name for name in paths if not (root / name).is_file()]
    duplicates = sorted(name for name, count in Counter(paths).items() if count > 1)
    current = {
        "README.md", "CHANGELOG.md", "docs/README.md", "docs/INDEX.md",
        "docs/开发/README.md", "docs/设计/README.md", "docs/history/README.md", "scripts/INDEX.md",
        catalog["authoritative_current_document"], "docs/开发/审计与证据治理.md",
    }
    # Local governance has one overview and one migration ledger; archived
    # records are reached through their path map rather than treated as current.
    for name in ("AGENTS.md", "workspace-governance/README.md",
                 "workspace-governance/迁移记录.md",
                 "archive/治理历史/migration-manifest-2026-10-30.json",
                 "archive/README.md", "archive/审计输入/README.md", ".local/README.md"):
        if (root / name).is_file():
            current.add(name)
    ledgers = [json.loads((root / name).read_text(encoding="utf-8")) for name in (
        "docs/开发/v0.6.0-深度审计覆盖.json",
        "docs/开发/v0.6.0-生产工作地图.json",
    )]
    missing_evidence = []
    unavailable_local_evidence = set()
    local_evidence_present = (root / ".local/evidence/v0.6.0").is_dir()
    local_root_present = (root / ".local").is_dir()
    archived_local_evidence = []
    archive_evidence_errors = []
    archive_manifests = []
    archive_manifest_indexed = False
    archive_moves = []
    archive_move_prefixes = []
    archive_moves_indexed = False
    archive_move_checked_paths = {}
    archive_move_integrity = {
        "checked_files": 0,
        "checked_bytes": 0,
        "errors": [],
    }
    archive_sha_cache = {}
    archive_member_cache = {}
    archive_resolution_cache = {}

    def load_archive_manifests() -> None:
        nonlocal archive_manifest_indexed
        if archive_manifest_indexed:
            return
        archive_manifest_indexed = True
        archive_root = (root / "archive").resolve()
        for manifest_path in archive_root.rglob("*.manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(manifest, dict):
                continue
            archive_value = manifest.get("archive")
            if not isinstance(archive_value, str) or not archive_value:
                continue
            archive_path = Path(archive_value)
            if not archive_path.is_absolute():
                rooted = root / archive_path
                beside_manifest = manifest_path.parent / archive_path
                archive_path = rooted if rooted.exists() or not beside_manifest.exists() else beside_manifest
            archive_path = archive_path.resolve()
            source_prefix = manifest.get("source_prefix") or manifest.get("source")
            if not isinstance(source_prefix, str) or not source_prefix:
                source_prefix = None
            prefix_member = zip_member(
                manifest.get("archive_member_prefix") or manifest.get("archive_prefix") or ""
            )
            entries = manifest.get("files") or manifest.get("entries") or manifest.get("members") or []
            if not isinstance(entries, list):
                continue
            manifest_relative = manifest_path.relative_to(root).as_posix()
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                source_value = (
                    entry.get("source_path") or entry.get("original_path") or entry.get("source")
                )
                path_value = entry.get("path") or entry.get("relative_path")
                if isinstance(source_value, str) and source_value:
                    source_path = Path(source_value)
                    if not source_path.is_absolute() and source_prefix:
                        source_path = Path(source_prefix) / source_path
                elif isinstance(path_value, str) and path_value:
                    source_path = Path(source_prefix) / path_value if source_prefix else Path(path_value)
                else:
                    continue
                member_value = (
                    entry.get("archive_member") or entry.get("member") or entry.get("entry")
                    or entry.get("archived_path")
                )
                if not member_value:
                    member_value = path_value
                member = zip_member(str(member_value)) if member_value else ""
                if member and prefix_member and not member.startswith(prefix_member.rstrip("/") + "/"):
                    member = f"{prefix_member.rstrip('/')}/{member}"
                archive_manifests.append({
                    "source": normalized_path(root, str(source_path)),
                    "member": member,
                    "bytes": entry.get("bytes", entry.get("size")),
                    "sha256": str(entry.get("sha256") or entry.get("sha") or "").lower(),
                    "manifest": manifest_relative,
                    "manifest_status": manifest.get("status"),
                    "zip_entries_verified": manifest.get("zip_entries_verified"),
                    "archive": archive_path,
                    "archive_sha256": str(manifest.get("archive_sha256") or "").lower(),
                })

    def load_archive_moves() -> None:
        """Index byte-identical directory moves recorded in the private receipt."""
        nonlocal archive_moves_indexed
        if archive_moves_indexed:
            return
        archive_moves_indexed = True
        mapping_path = root / "archive/路径映射.json"
        if not mapping_path.is_file():
            return
        try:
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if mapping.get("schema") != "zhiyi.local-path-migrations.v1":
            return
        for mapping_entry in mapping.get("entries", []):
            if not isinstance(mapping_entry, dict) or mapping_entry.get("kind") != "move":
                continue
            source_prefix = mapping_entry.get("source")
            target_prefix = mapping_entry.get("target")
            receipt_value = mapping_entry.get("receipt")
            if not all(isinstance(value, str) and value for value in (source_prefix, target_prefix, receipt_value)):
                continue
            if Path(receipt_value.replace("\\", "/")).name != "historical-evidence-moves.json":
                continue
            source_prefix_key = normalized_path(root, source_prefix)
            source_prefix_path = Path(source_prefix)
            if not source_prefix_path.is_absolute():
                source_prefix_path = root / source_prefix_path
            source_prefix_path = source_prefix_path.resolve()
            receipt_path = Path(receipt_value)
            if not receipt_path.is_absolute():
                receipt_path = root / receipt_path
            prefix_record = {
                "source": source_prefix_key,
                "source_path": source_prefix_path,
                "receipt": receipt_path,
                "mapping_status": mapping_entry.get("status"),
                "error": None,
            }
            archive_move_prefixes.append(prefix_record)
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                prefix_record["error"] = "move receipt is missing or invalid"
                archive_move_integrity["errors"].append({
                    "source": source_prefix,
                    "receipt": str(receipt_path),
                    "error": prefix_record["error"],
                })
                continue
            if receipt.get("status") not in {"completed", "moved-and-hash-verified"}:
                prefix_record["error"] = "move receipt is not complete"
                archive_move_integrity["errors"].append({
                    "source": source_prefix,
                    "receipt": str(receipt_path),
                    "error": prefix_record["error"],
                })
                continue
            target_prefix_path = Path(target_prefix)
            if not target_prefix_path.is_absolute():
                target_prefix_path = root / target_prefix_path
            target_prefix_path = target_prefix_path.resolve()
            prefix_record["target_path"] = target_prefix_path
            if not target_prefix_path.is_relative_to((root / "archive").resolve()):
                prefix_record["error"] = "move target escapes workspace archive"
                archive_move_integrity["errors"].append({
                    "source": source_prefix,
                    "receipt": str(receipt_path),
                    "error": prefix_record["error"],
                })
                continue
            matching_targets = [
                item for item in receipt.get("targets", [])
                if isinstance(item, dict)
                and normalized_path(root, str(item.get("source", ""))) == source_prefix_key
                and normalized_path(root, str(item.get("target", "")))
                    == normalized_path(root, str(target_prefix_path))
            ]
            if not matching_targets:
                prefix_record["error"] = "move receipt has no matching source/target entry"
                archive_move_integrity["errors"].append({
                    "source": source_prefix,
                    "receipt": str(receipt_path),
                    "error": prefix_record["error"],
                })
                continue
            for moved in matching_targets:
                if moved.get("status") not in {"moved-and-hash-verified", "verified-source-and-archive", "verified"}:
                    prefix_record["error"] = "moved tree status is not verified"
                    archive_move_integrity["errors"].append({
                        "source": source_prefix,
                        "receipt": str(receipt_path),
                        "error": prefix_record["error"],
                    })
                    continue
                source_directory = Path(str(moved["source"]))
                target_directory = Path(str(moved["target"]))
                records = moved.get("files", [])
                if not isinstance(records, list):
                    continue
                try:
                    expected_count = int(moved.get("file_count", len(records)))
                    expected_bytes = int(moved.get("bytes", sum(int(item.get("bytes", -1)) for item in records)))
                    actual_bytes = sum(int(item.get("bytes", -1)) for item in records if isinstance(item, dict))
                except (TypeError, ValueError):
                    continue
                if expected_count != len(records) or actual_bytes != expected_bytes:
                    prefix_record["error"] = "move receipt file count or byte total is inconsistent"
                    archive_move_integrity["errors"].append({
                        "source": source_prefix,
                        "receipt": str(receipt_path),
                        "error": prefix_record["error"],
                    })
                    continue
                for record in records:
                    if not isinstance(record, dict):
                        prefix_record["error"] = "move receipt contains a non-object file record"
                        archive_move_integrity["errors"].append({
                            "source": source_prefix,
                            "receipt": str(receipt_path),
                            "error": prefix_record["error"],
                        })
                        continue
                    relative_value = record.get("path")
                    byte_count = record.get("bytes")
                    digest = str(record.get("sha256") or "").lower()
                    if not isinstance(relative_value, str) or byte_count is None or not digest:
                        prefix_record["error"] = "move receipt file record is missing its path, size or SHA-256"
                        archive_move_integrity["errors"].append({
                            "source": source_prefix,
                            "receipt": str(receipt_path),
                            "error": prefix_record["error"],
                        })
                        continue
                    relative_path = Path(relative_value)
                    if relative_path.is_absolute() or ".." in relative_path.parts:
                        prefix_record["error"] = "move receipt file path is absolute or escapes its tree"
                        archive_move_integrity["errors"].append({
                            "source": source_prefix,
                            "receipt": str(receipt_path),
                            "error": prefix_record["error"],
                        })
                        continue
                    source_path = (source_directory / relative_path).resolve()
                    target_path = (target_directory / relative_path).resolve()
                    if not source_path.is_relative_to((root / ".local").resolve()):
                        prefix_record["error"] = "move receipt source escapes workspace .local"
                        archive_move_integrity["errors"].append({
                            "source": str(source_path),
                            "receipt": str(receipt_path),
                            "error": prefix_record["error"],
                        })
                        continue
                    if not target_path.is_relative_to((root / "archive").resolve()):
                        prefix_record["error"] = "move receipt target escapes workspace archive"
                        archive_move_integrity["errors"].append({
                            "source": str(source_path),
                            "archive": str(target_path),
                            "receipt": str(receipt_path),
                            "error": prefix_record["error"],
                        })
                        continue
                    target_key = str(target_path).casefold()
                    if target_key not in archive_move_checked_paths:
                        archive_move_integrity["checked_files"] += 1
                        verified = False
                        try:
                            if not target_path.is_file():
                                raise OSError("archived file is missing")
                            actual_bytes = target_path.stat().st_size
                            actual_sha = sha256_file(target_path)
                            if actual_bytes != int(byte_count) or actual_sha != digest:
                                raise OSError("archived file size/SHA-256 differs from receipt")
                            archive_move_integrity["checked_bytes"] += actual_bytes
                            verified = True
                        except (OSError, TypeError, ValueError) as exception:
                            archive_move_integrity["errors"].append({
                                "source": str(source_path),
                                "archive": str(target_path),
                                "receipt": str(receipt_path),
                                "error": str(exception),
                            })
                        archive_move_checked_paths[target_key] = verified
                    verified = archive_move_checked_paths[target_key]
                    archive_moves.append({
                        "source": normalized_path(root, str(source_path)),
                        "source_display": str(source_path),
                        "archive_file": target_path,
                        "bytes": int(byte_count),
                        "sha256": digest,
                        "receipt": receipt_path,
                        "mapping_status": mapping_entry.get("status"),
                        "verified": verified,
                    })

    def resolve_archived_evidence(name: str):
        source_key = normalized_path(root, name)
        if source_key in archive_resolution_cache:
            return archive_resolution_cache[source_key]
        load_archive_manifests()
        load_archive_moves()
        candidates = [item for item in archive_manifests if item["source"] == source_key]
        failures = []
        for item in candidates:
            status = item["manifest_status"]
            zip_count = item["zip_entries_verified"]
            zip_verified = zip_count is True or (isinstance(zip_count, int) and zip_count > 0)
            if status not in {"verified-source-and-archive", "verified", "archive-verified"} and not zip_verified:
                failures.append(f"manifest status is not verified: {item['manifest']}")
                continue
            if not item["archive"].is_relative_to((root / "archive").resolve()):
                failures.append(f"archive path escapes workspace archive: {item['manifest']}")
                continue
            archive_path = item["archive"]
            if not archive_path.is_file():
                failures.append(f"archive file is missing: {item['manifest']}")
                continue
            expected_archive_sha = item["archive_sha256"]
            if not expected_archive_sha:
                failures.append(f"archive SHA-256 is absent: {item['manifest']}")
                continue
            archive_key = str(archive_path).casefold()
            if archive_key not in archive_sha_cache:
                try:
                    archive_sha_cache[archive_key] = sha256_file(archive_path)
                except OSError as error:
                    archive_sha_cache[archive_key] = f"error:{error.__class__.__name__}"
            if archive_sha_cache[archive_key] != expected_archive_sha:
                failures.append(f"archive SHA-256 mismatch: {item['manifest']}")
                continue
            if not item["member"] or not item["sha256"] or item["bytes"] is None:
                failures.append(f"member path, byte count or SHA-256 is absent: {item['manifest']}")
                continue
            member_key = (archive_key, item["member"], int(item["bytes"]))
            if member_key not in archive_member_cache:
                try:
                    with zipfile.ZipFile(archive_path) as archive:
                        info = archive.getinfo(item["member"])
                        if info.is_dir() or info.file_size != int(item["bytes"]):
                            archive_member_cache[member_key] = "size-mismatch"
                        else:
                            digest = hashlib.sha256()
                            with archive.open(info) as stream:
                                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                    digest.update(chunk)
                            archive_member_cache[member_key] = digest.hexdigest()
                except (OSError, KeyError, zipfile.BadZipFile, ValueError, TypeError) as error:
                    archive_member_cache[member_key] = f"error:{error.__class__.__name__}"
            member_result = archive_member_cache[member_key]
            if member_result != item["sha256"]:
                failures.append(f"archive member is absent or its size/SHA-256 differs: {item['manifest']}#{item['member']}")
                continue
            result = ({
                "source": name,
                "source_original_present": False,
                "manifest": item["manifest"],
                "archive": archive_path.relative_to(root).as_posix(),
                "member": item["member"],
                "archive_sha256": "verified",
                "member_sha256": "verified",
                "meaning": "location and preservation only; no product test was run or certified",
            }, None)
            archive_resolution_cache[source_key] = result
            return result
        error = "; ".join(failures) if failures else None
        move_candidates = [item for item in archive_moves if item["source"] == source_key]
        move_failures = []
        for item in move_candidates:
            if not item["verified"]:
                move_failures.append(f"moved evidence file did not pass receipt verification: {item['receipt']}")
                continue
            archive_file = item["archive_file"]
            if not archive_file.is_file():
                move_failures.append(f"moved evidence file is missing: {item['receipt']}")
                continue
            try:
                actual_bytes = archive_file.stat().st_size
                actual_sha = sha256_file(archive_file)
            except OSError as exception:
                move_failures.append(f"moved evidence file cannot be read: {exception.__class__.__name__}")
                continue
            if actual_bytes != item["bytes"] or actual_sha != item["sha256"]:
                move_failures.append(f"moved evidence size/SHA-256 differs: {item['receipt']}")
                continue
            result = ({
                "source": name,
                "source_original_present": False,
                "manifest": item["receipt"].relative_to(root).as_posix(),
                "archive": archive_file.relative_to(root).as_posix(),
                "member": "",
                "archive_sha256": "not-applicable-directory-move",
                "archived_file_sha256": "verified-by-file-receipt",
                "member_sha256": "verified",
                "meaning": "location and byte integrity only; no product test was run or certified",
            }, None)
            archive_resolution_cache[source_key] = result
            return result
        directory_prefixes = [
            prefix for prefix in archive_move_prefixes
            if path_is_same_or_child(source_key, prefix["source"])
        ]
        for prefix in directory_prefixes:
            if prefix["error"]:
                continue
            try:
                requested_path = Path(name)
                if not requested_path.is_absolute():
                    requested_path = root / requested_path
                requested_path = requested_path.resolve()
                relative_directory = requested_path.relative_to(prefix["source_path"])
            except (OSError, ValueError):
                continue
            archived_directory = (prefix["target_path"] / relative_directory).resolve()
            if not archived_directory.is_relative_to((root / "archive").resolve()):
                continue
            descendants = [
                item for item in archive_moves
                if item["source"] != source_key
                and path_is_same_or_child(item["source"], source_key)
            ]
            if not archived_directory.is_dir():
                move_failures.append(f"moved evidence directory is missing: {prefix['receipt']}")
                continue
            if any(not item["verified"] for item in descendants):
                move_failures.append(f"one or more files below the moved evidence directory failed SHA verification: {prefix['receipt']}")
                continue
            result = ({
                "source": name,
                "source_original_present": False,
                "manifest": prefix["receipt"].relative_to(root).as_posix(),
                "archive": archived_directory.relative_to(root).as_posix(),
                "member": "",
                "archive_sha256": "not-applicable-directory-move",
                "member_sha256": "verified",
                "archived_files": len(descendants),
                "meaning": "location and byte integrity only; no product test was run or certified",
            }, None)
            archive_resolution_cache[source_key] = result
            return result
        if move_failures:
            error = "; ".join(move_failures)
        else:
            matching_prefixes = [
                prefix for prefix in archive_move_prefixes
                if path_is_same_or_child(source_key, prefix["source"])
            ]
            if matching_prefixes:
                prefix_errors = [prefix["error"] for prefix in matching_prefixes if prefix["error"]]
                error = "; ".join(prefix_errors) if prefix_errors else (
                    "source path is under a moved evidence tree but absent from its verified file receipt"
                )
        result = (None, error)
        archive_resolution_cache[source_key] = result
        return result

    def check_artifact(name: str) -> None:
        path = Path(name)
        resolved = path if path.is_absolute() else root / path
        if resolved.is_file():
            return
        local_path = resolved.resolve().is_relative_to((root / ".local").resolve())
        if local_path:
            archived, error = resolve_archived_evidence(name)
            if archived:
                archived_local_evidence.append(archived)
                return
            if error:
                archive_evidence_errors.append({"source": name, "error": error})
                missing_evidence.append(name)
                return
            if not local_root_present or (str(name).replace("\\", "/").startswith(".local/evidence/") and not local_evidence_present):
                unavailable_local_evidence.add(name)
                return
        missing_evidence.append(name)

    def check_registered_artifacts(value) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"artifact", "manifest"} and isinstance(child, str):
                    check_artifact(child)
                else:
                    check_registered_artifacts(child)
        elif isinstance(value, list):
            for child in value:
                check_registered_artifacts(child)

    for ledger in ledgers:
        if ledger.get("summary_report"):
            current.add(ledger["summary_report"])
        check_registered_artifacts(ledger)
        for stream in ledger["workstreams"]:
            report = root / stream["report"]
            if report.is_file():
                current.add(stream["report"])
            elif stream["status"] not in {"planned", "running", "unverified"}:
                missing.append(stream["report"])
    broken = []
    for name in sorted(current):
        document = root / name
        if not document.is_file():
            missing.append(name)
            continue
        for raw in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            target = unquote(raw.strip("<>").split("#", 1)[0])
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (document.parent / target).resolve()
            if resolved.exists():
                continue
            if resolved.is_relative_to((root / ".local").resolve()):
                source = resolved.relative_to(root).as_posix()
                archived, error = resolve_archived_evidence(source)
                if archived:
                    archived_local_evidence.append(archived)
                    continue
                if error:
                    archive_evidence_errors.append({"source": source, "error": error})
                    broken.append({"file": name, "target": target})
                    continue
                # Private evidence can be absent in a public checkout. Record
                # the gap explicitly; this is not a test result or a pass.
                if not local_root_present or not local_evidence_present:
                    unavailable_local_evidence.add(source)
                    continue
            broken.append({"file": name, "target": target})
    unique_archived = {
        (item["source"], item["manifest"], item["member"]): item
        for item in archived_local_evidence
    }
    unique_archive_errors = {
        (item["source"], item["error"]): item for item in archive_evidence_errors
    }
    result = {
        "status_entry_count": len(paths),
        "current_documents_checked": len(current),
        "missing_documents": sorted(set(missing)),
        "duplicate_documents": duplicates,
        "broken_current_links": broken,
        "missing_local_evidence": sorted(set(missing_evidence)),
        "unavailable_local_evidence": sorted(unavailable_local_evidence),
        "archived_local_evidence": sorted(unique_archived.values(), key=lambda item: item["source"]),
        "archive_evidence_errors": sorted(unique_archive_errors.values(), key=lambda item: item["source"]),
        "archive_moved_file_integrity": {
            "checked_files": archive_move_integrity["checked_files"],
            "checked_bytes": archive_move_integrity["checked_bytes"],
            "errors": archive_move_integrity["errors"],
            "meaning": "Moved local evidence file preservation only; not product test evidence.",
        },
        "product_tests_run": False,
        "scope": "Documentation links, catalog paths and preservation mappings only. Archive verification proves location and byte integrity, not a product test or acceptance result.",
    }
    result["passed"] = not any((
        missing, duplicates, broken, missing_evidence, archive_evidence_errors,
        archive_move_integrity["errors"],
    ))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Optional new evidence file inside workspace .local.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = args.report.resolve() if args.report else None
    if target and (not target.is_relative_to(root / ".local") or target.exists()):
        parser.error("The report must be a new file inside this workspace's .local directory.")
    result = check(root)
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(output)
    print(output, end="")
    raise SystemExit(not result["passed"])


if __name__ == "__main__":
    main()
