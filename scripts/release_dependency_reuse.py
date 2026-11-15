"""Fail-closed input and Python-source checks for explicit dependency reuse."""

from __future__ import annotations

import argparse
import ast
import hashlib
from importlib import metadata
import importlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import platform
import re
import sys
import tomllib

from packaging.requirements import Requirement
from packaging.version import Version


LOCKED_INPUTS = (
    "package.json",
    "package-lock.json",
    "apps/web/package.json",
    "apps/api/pyproject.toml",
    "apps/api/uv.lock",
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _platform_cpu() -> str:
    return {
        "amd64": "x64", "x86_64": "x64", "aarch64": "arm64",
        "arm64": "arm64", "x86": "ia32", "i386": "ia32", "i686": "ia32",
    }.get(platform.machine().lower(), platform.machine().lower())


def _matches_platform(restrictions: object, current: str) -> bool:
    if restrictions is None:
        return True
    if not isinstance(restrictions, list) or not all(isinstance(item, str) and item for item in restrictions):
        raise ValueError("Invalid npm platform restriction")
    positive = [item for item in restrictions if not item.startswith("!")]
    negative = [item[1:] for item in restrictions if item.startswith("!")]
    return current not in negative and (not positive or current in positive)


def package_applies(record: dict) -> bool:
    """Only skip an explicitly optional dependency excluded by this platform."""
    compatible = _matches_platform(record.get("os"), sys.platform) and _matches_platform(
        record.get("cpu"), _platform_cpu()
    )
    return compatible or not record.get("optional", False)


def checked_package_dir(dependency_root: Path, package_path: str) -> Path:
    relative = PurePosixPath(package_path)
    if (
        relative.is_absolute() or not package_path.startswith("node_modules/")
        or any(part in ("", ".", "..") or "\\" in part or ":" in part for part in relative.parts)
    ):
        raise ValueError("Unsafe npm package path")
    root = (dependency_root / "node_modules").resolve(strict=True)
    path = dependency_root.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"Dependency path escapes node_modules: {package_path}")
    return path


def node_inventory(candidate: Path, dependency_root: Path) -> dict:
    lock = json.loads((candidate / "package-lock.json").read_text(encoding="utf-8"))
    if lock.get("lockfileVersion", 0) < 2 or not isinstance(lock.get("packages"), dict):
        raise ValueError("A modern npm lockfile with a package inventory is required")
    records = []
    skipped = 0
    absent_optional_dev = 0
    for package_path, package in sorted(lock["packages"].items()):
        if not package_path.startswith("node_modules/"):
            continue
        if not isinstance(package, dict):
            raise ValueError("Invalid npm lock package record")
        if package.get("link"):
            # The workspace link can point at the main tree. It is never an
            # allowed dependency source for application modules.
            if package_path != "node_modules/@document-pipeline/web":
                raise ValueError(f"Unexpected workspace package link: {package_path}")
            continue
        if not package_applies(package):
            skipped += 1
            continue
        package_dir = checked_package_dir(dependency_root, package_path)
        manifest = package_dir / "package.json"
        if not manifest.is_file():
            # Some optional build-tool dependencies are transitive children of
            # a foreign-platform binary and have no own os/cpu restrictions.
            # An actual required tool or import is still checked by the build.
            if package.get("optional") and package.get("dev"):
                absent_optional_dev += 1
                continue
            raise ValueError(f"Installed dependency is missing: {package_path}")
        value = json.loads(manifest.read_text(encoding="utf-8"))
        expected_name = package.get("name") or package_path.rsplit("node_modules/", 1)[-1]
        if value.get("version") != package.get("version") or value.get("name") != expected_name:
            raise ValueError(f"Installed dependency identity differs from lock: {package_path}")
        records.append({"path": package_path, "version": value["version"], "manifest_sha256": digest(manifest)})
    if not records:
        raise ValueError("The installed dependency inventory is empty")
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "installed_packages": len(records),
        "incompatible_optional_packages_skipped": skipped,
        "absent_optional_build_packages": absent_optional_dev,
        "package_manifest_inventory_sha256": hashlib.sha256(encoded).hexdigest(),
        "identity_scope": "lock and installed package manifests; not a clean install or a hash of all dependency contents",
    }


def workspace_inputs(candidate: Path, dependency_root: Path) -> dict:
    candidate = candidate.absolute()
    dependency_root = dependency_root.absolute()
    if _is_link(dependency_root) or _is_link(candidate):
        raise ValueError("Workspace and candidate roots must not be links")
    if candidate == dependency_root or not candidate.is_relative_to(dependency_root):
        raise ValueError("Candidate must be a separate directory beneath the dependency workspace")
    cursor = dependency_root
    for part in candidate.relative_to(dependency_root).parts:
        cursor = cursor / part
        if _is_link(cursor):
            raise ValueError("Candidate path must not traverse a link")
        if cursor != candidate and (cursor / "package.json").exists():
            raise ValueError("An intermediate package.json could change dependency resolution")
    candidate = candidate.resolve(strict=True)
    dependency_root = dependency_root.resolve(strict=True)
    if _is_link(dependency_root / "node_modules"):
        raise ValueError("The workspace node_modules root must not be a link")
    for relative in ("node_modules", "apps/node_modules", "apps/web/node_modules"):
        path = candidate / relative
        if path.exists() or _is_link(path):
            raise ValueError(f"Candidate has an unexpected node_modules directory: {relative}")
    hashes = {}
    for name in LOCKED_INPUTS:
        first, second = candidate / name, dependency_root / name
        for base in (candidate, dependency_root):
            cursor = base
            for part in PurePosixPath(name).parts:
                cursor = cursor / part
                if _is_link(cursor):
                    raise ValueError(f"Workspace input must not traverse a link: {name}")
        if _is_link(first) or _is_link(second) or not first.is_file() or not second.is_file():
            raise ValueError(f"Required workspace input is missing or linked: {name}")
        first_hash, second_hash = digest(first), digest(second)
        if first_hash != second_hash:
            raise ValueError(f"Candidate and workspace input differ: {name}")
        hashes[name] = first_hash
    return hashes


def preflight(candidate: Path, dependency_root: Path) -> dict:
    hashes = workspace_inputs(candidate, dependency_root)
    candidate, dependency_root = candidate.resolve(strict=True), dependency_root.resolve(strict=True)
    expected_python = dependency_root / "apps/api/.venv/Scripts/python.exe"
    if not expected_python.is_file() or Path(sys.executable).resolve() != expected_python.resolve():
        raise ValueError("Reuse preflight must run with the workspace API virtual environment Python")
    api_source = candidate / "apps/api/src"
    if any(entry.name != "document_pipeline_api" for entry in api_source.iterdir()):
        raise ValueError("Unexpected top-level Python source could shadow workspace dependencies")
    package = importlib.import_module("document_pipeline_api")
    source = Path(package.__file__).resolve(strict=True)
    expected_source = (candidate / "apps/api/src/document_pipeline_api").resolve(strict=True)
    if not expected_source.is_relative_to(candidate) or not source.is_relative_to(expected_source):
        raise ValueError("Python application package resolved outside the candidate")
    project = tomllib.loads((candidate / "apps/api/pyproject.toml").read_text(encoding="utf-8"))
    declared = {Requirement(item).name.lower(): Requirement(item) for item in project["dependency-groups"]["dev"]}
    tools = {}
    for name, module_name in (("pyinstaller", "PyInstaller"), ("pip-audit", "pip_audit")):
        requirement = declared.get(name)
        installed = metadata.version(name)
        if requirement is None or Version(installed) not in requirement.specifier:
            raise ValueError(f"Installed build tool differs from candidate project: {name}")
        spec = importlib.util.find_spec(module_name)
        site_packages = (expected_python.parent.parent / "Lib/site-packages").resolve(strict=True)
        if spec is None or not spec.origin or not Path(spec.origin).resolve(strict=True).is_relative_to(site_packages):
            raise ValueError(f"Python build tool resolved outside the workspace virtual environment: {name}")
        tools[name] = installed
    inventory = node_inventory(candidate, dependency_root)
    node_versions = {}
    for name in ("typescript", "vite"):
        manifest = dependency_root / "node_modules" / name / "package.json"
        node_versions[name] = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    return {
        "mode": "reused-existing-workspace-dependencies",
        "local_workspace_root": str(dependency_root),
        "input_sha256": hashes,
        "node_inventory": inventory,
        "node_tool_versions": node_versions,
        "python_version": platform.python_version(),
        "python_tool_versions": tools,
        "python_application_source": "candidate/apps/api/src/document_pipeline_api",
    }


def python_runtime_inventory(requirements: Path) -> dict:
    raw = requirements.read_text(encoding="utf-8-sig")
    lines = re.sub(r"\\\r?\n", " ", raw).splitlines()
    selected = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        declaration = re.sub(r"\s+--hash=sha256:[0-9a-fA-F]{64}(?=\s|$)", "", line).strip()
        requirement = Requirement(declaration)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        pins = list(requirement.specifier)
        if requirement.url or len(pins) != 1 or pins[0].operator != "==" or "*" in pins[0].version:
            raise ValueError(f"Python runtime requirement is not exactly pinned: {requirement.name}")
        name = re.sub(r"[-_.]+", "-", requirement.name).lower()
        version = metadata.version(requirement.name)
        if Version(version) != Version(pins[0].version) or (name in selected and selected[name] != version):
            raise ValueError(f"Installed Python runtime dependency differs from frozen lock: {name}")
        selected[name] = version
    if not selected:
        raise ValueError("Frozen Python runtime inventory is empty")
    data = json.dumps(selected, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"installed_runtime_packages": len(selected), "runtime_version_inventory_sha256": hashlib.sha256(data).hexdigest()}


def verify_pyinstaller_analysis(candidate: Path, dependency_root: Path, analysis_toc: Path) -> dict:
    candidate = candidate.resolve(strict=True)
    dependency_root = dependency_root.resolve(strict=True)
    app_root = (candidate / "apps/api/src/document_pipeline_api").resolve(strict=True)
    main_source = (dependency_root / "apps/api/src").resolve(strict=True)
    toc = ast.literal_eval(analysis_toc.read_text(encoding="utf-8"))
    records = []

    def visit(value: object) -> None:
        if isinstance(value, (tuple, list)):
            if len(value) == 3 and all(isinstance(part, str) for part in value):
                name, source_name, _ = value
                source = Path(source_name)
                is_app = name == "document_pipeline_api" or name.startswith("document_pipeline_api.")
                if is_app and (not source.is_absolute() or not source.is_file()):
                    raise ValueError(f"PyInstaller application module has no verifiable source: {name}")
                if source.is_absolute() and source.exists():
                    resolved = source.resolve(strict=True)
                    if is_app and not resolved.is_relative_to(app_root):
                        raise ValueError(f"PyInstaller application module resolved outside candidate: {name}")
                    if resolved.is_relative_to(main_source) and not resolved.is_relative_to(app_root):
                        raise ValueError("PyInstaller included application source from the dependency workspace")
                    if is_app:
                        records.append({"name": name, "path": resolved.relative_to(app_root).as_posix(), "sha256": digest(resolved)})
            for entry in value:
                visit(entry)
        elif isinstance(value, dict):
            for entry in value.values():
                visit(entry)

    visit(toc)
    if not records:
        raise ValueError("PyInstaller analysis did not identify any candidate application modules")
    encoded = json.dumps(sorted(records, key=lambda record: (record["name"], record["path"])), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"application_modules": len(records), "application_source_inventory_sha256": hashlib.sha256(encoded).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("preflight", "verify-analysis", "verify-python-deps"))
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--dependency-root", type=Path)
    parser.add_argument("--analysis-toc", type=Path)
    parser.add_argument("--requirements", type=Path)
    args = parser.parse_args()
    if args.operation == "preflight":
        if args.candidate is None or args.dependency_root is None:
            parser.error("--candidate and --dependency-root are required for preflight")
        result = preflight(args.candidate, args.dependency_root)
    elif args.operation == "verify-python-deps":
        if args.requirements is None:
            parser.error("--requirements is required for verify-python-deps")
        result = python_runtime_inventory(args.requirements)
    else:
        if args.analysis_toc is None or args.candidate is None or args.dependency_root is None:
            parser.error("--candidate, --dependency-root and --analysis-toc are required for verify-analysis")
        result = verify_pyinstaller_analysis(args.candidate, args.dependency_root, args.analysis_toc)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
