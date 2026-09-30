import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import shutil
import subprocess
from packaging.requirements import Requirement
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5


LICENSE_NAMES = re.compile(r"^(license|licence|copying|notice)", re.IGNORECASE)
FORBIDDEN_LICENSE = re.compile(r"(?:^|[^A-Z])(AGPL|SSPL|BUSL)(?:[^A-Z]|$)", re.IGNORECASE)


def _normalized(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def _license_for_distribution(distribution: metadata.Distribution) -> str:
    declared = distribution.metadata.get("License-Expression") or distribution.metadata.get(
        "License"
    )
    if declared and declared.strip() and declared.strip().lower() != "unknown":
        return " ".join(declared.split())
    classifiers = distribution.metadata.get_all("Classifier") or []
    licenses = [
        value.removeprefix("License :: OSI Approved :: ")
        for value in classifiers
        if value.startswith("License :: OSI Approved :: ")
    ]
    if licenses:
        return " / ".join(licenses)
    for relative in distribution.files or []:
        if not any(LICENSE_NAMES.match(part) for part in relative.parts):
            continue
        source = distribution.locate_file(relative)
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8", errors="ignore")[:4096]
        if "Permission is hereby granted, free of charge" in text:
            return "MIT"
    return "未声明"


def _runtime_python_versions(requirements: Path, audit: Path) -> dict[str, str]:
    """Use the same frozen, extras-resolved export as the vulnerability audit."""
    raw = requirements.read_text(encoding="utf-8-sig")
    logical_lines = re.sub(r"\\\r?\n", " ", raw).splitlines()
    selected: dict[str, str] = {}
    for line in logical_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        declaration = re.sub(r"\s+--hash=sha256:[0-9a-fA-F]{64}(?=\s|$)", "", line).strip()
        requirement = Requirement(declaration)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        pins = list(requirement.specifier)
        if requirement.url or len(pins) != 1 or pins[0].operator != "==" or "*" in pins[0].version:
            raise RuntimeError(f"Runtime requirement is not exactly pinned: {requirement.name}")
        name, version = _normalized(requirement.name), pins[0].version
        if name in selected and selected[name] != version:
            raise RuntimeError(f"Conflicting runtime versions: {name}")
        selected[name] = version
    if not selected:
        raise RuntimeError("The frozen runtime dependency export is empty")
    audited: dict[str, str] = {}
    for item in json.loads(audit.read_text(encoding="utf-8-sig"))["dependencies"]:
        if item.get("skip_reason") or item.get("vulns"):
            raise RuntimeError(f"Runtime dependency was skipped or has advisories: {item['name']}")
        name = _normalized(item["name"])
        if name in audited:
            raise RuntimeError(f"Duplicate dependency audit record: {name}")
        audited[name] = item["version"]
    if selected != audited:
        raise RuntimeError("Frozen runtime requirements and dependency audit identities differ")
    return selected


def _copy_python_licenses(
    output: Path, selected_versions: dict[str, str]
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    distributions = {
        _normalized(item.metadata.get("Name") or item.name): item
        for item in metadata.distributions()
    }
    missing = sorted(selected_versions.keys() - distributions.keys())
    if missing:
        raise RuntimeError(f"Python runtime dependencies are not installed: {missing}")
    for normalized_name in sorted(selected_versions):
        distribution = distributions[normalized_name]
        if distribution.version != selected_versions[normalized_name]:
            raise RuntimeError(f"Installed dependency differs from frozen input: {normalized_name}")
        name = distribution.metadata.get("Name") or distribution.name
        rows.append(
            {
                "ecosystem": "python",
                "name": name,
                "version": distribution.version,
                "license": _license_for_distribution(distribution),
            }
        )
        copied = False
        for relative in distribution.files or []:
            if not any(LICENSE_NAMES.match(part) for part in relative.parts):
                continue
            source = distribution.locate_file(relative)
            if not source.is_file():
                continue
            destination = output / "python" / _safe_name(
                f"{name}-{distribution.version}-{Path(relative).name}"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            copied = True
        if not copied:
            # Some old sdists omitted their upstream license. Only a versioned,
            # locally preserved and hash-checked provenance entry can fill it.
            license_root = Path(__file__).resolve().parents[1] / "packaging" / "licenses"
            overrides = json.loads((license_root / "python-overrides.json").read_text(encoding="utf-8"))
            override = overrides.get(f"{normalized_name}=={distribution.version}")
            if not override:
                raise RuntimeError(f"Python runtime dependency has no license file: {normalized_name}")
            source = license_root / override["file"]
            if source.parent != license_root or source.is_symlink():
                raise RuntimeError("Invalid preserved upstream license path")
            if hashlib.sha256(source.read_bytes()).hexdigest() != override["sha256"]:
                raise RuntimeError("Preserved upstream license digest differs")
            destination = output / "python" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            rows[-1]["license"] = override["license"]
            (destination.with_suffix(".provenance.json")).write_text(
                json.dumps(override, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    return rows


def _copy_node_licenses(root: Path, output: Path) -> list[dict[str, str]]:
    lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
    rows: list[dict[str, str]] = []
    for package_path, package in sorted(lock.get("packages", {}).items()):
        if not package_path.startswith("node_modules/") or package.get("dev") is True:
            continue
        name = package_path.removeprefix("node_modules/")
        if name == "@document-pipeline/web":
            continue
        version = str(package.get("version", "unknown"))
        package_dir = root / package_path
        license_name = package.get("license")
        package_json = package_dir / "package.json"
        if not license_name and package_json.is_file():
            license_name = json.loads(package_json.read_text(encoding="utf-8")).get("license")
        rows.append(
            {
                "ecosystem": "node",
                "name": name,
                "version": version,
                "license": str(license_name or "未声明"),
            }
        )
        if not package_dir.is_dir():
            continue
        for source in package_dir.iterdir():
            if not source.is_file() or not LICENSE_NAMES.match(source.name):
                continue
            destination = output / "node" / _safe_name(
                f"{name}-{version}-{source.name}"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    return rows


def _copy_build_tool_licenses(output: Path, executable: Path | None) -> list[dict[str, str]]:
    if executable is None:
        return []
    executable = executable.resolve(strict=True)
    version = subprocess.check_output([str(executable), "/VERSION"], timeout=10).decode().strip()
    if not re.fullmatch(r"v?\d+(?:\.\d+)+(?:[A-Za-z0-9._-]*)", version):
        raise RuntimeError("Cannot identify the actual NSIS compiler version")
    candidates = (executable.parent / "COPYING", executable.parent / "Docs" / "AppendixI.html")
    nsis_license = next((path for path in candidates if path.is_file()), None)
    if nsis_license is None:
        raise RuntimeError("The selected NSIS compiler's license document is missing")
    destination = output / "build-tools" / f"NSIS-{_safe_name(version)}-{nsis_license.name}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(nsis_license, destination)
    return [
        {
            "ecosystem": "build-tool",
            "name": "NSIS",
            "version": version.removeprefix("v"),
            "license": "zlib/libpng and bundled compression-module licenses",
        }
    ]


def _write_sbom(output: Path, rows: list[dict[str, str]], version: str, revision: str) -> None:
    components = []
    for row in rows:
        if row["ecosystem"] == "build-tool":
            purl_type = "generic"
        elif row["ecosystem"] == "python":
            purl_type = "pypi"
        else:
            purl_type = "npm"
        purl = f"pkg:{purl_type}/{quote(row['name'], safe='@/') }@{quote(row['version'])}"
        components.append(
            {
                "type": "library" if purl_type != "generic" else "application",
                "bom-ref": purl,
                "name": row["name"],
                "version": row["version"],
                "purl": purl,
                "licenses": [{"license": {"name": row["license"]}}],
                "properties": [
                    {"name": "document-pipeline:ecosystem", "value": row["ecosystem"]}
                ],
            }
        )
    serial_seed = f"document-pipeline:{version}:{revision}"
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid5(NAMESPACE_URL, serial_seed)}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": f"pkg:generic/document-pipeline@{quote(version)}",
                "name": "Document Pipeline",
                "version": version,
                "properties": [{"name": "vcs:revision", "value": revision}],
            }
        },
        "components": components,
    }
    (output / "SBOM.cdx.json").write_text(
        json.dumps(bom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="收集发布包的第三方许可证与 SBOM")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--fail-on-policy", action="store_true")
    parser.add_argument("--nsis-executable", type=Path)
    parser.add_argument("--python-requirements", type=Path, required=True)
    parser.add_argument("--python-audit", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    rows = _copy_python_licenses(
        output, _runtime_python_versions(args.python_requirements, args.python_audit)
    )
    rows.extend(_copy_node_licenses(root, output))
    rows.extend(_copy_build_tool_licenses(output, args.nsis_executable))
    rows.sort(key=lambda row: (row["ecosystem"], row["name"].lower(), row["version"]))
    lines = [
        "# 第三方依赖清单",
        "",
        "本文件由构建脚本按运行时依赖生成。许可证全文位于同目录子目录。",
        "",
        "| 生态 | 名称 | 版本 | 许可证声明 |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {row['ecosystem']} | {row['name']} | {row['version']} | "
        f"{row['license'].replace('|', '/')} |"
        for row in rows
    )
    (output / "THIRD_PARTY_NOTICES.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    _write_sbom(output, rows, args.version, args.revision)

    unknown = [
        f"{row['ecosystem']}:{row['name']}@{row['version']}"
        for row in rows
        if row["license"] == "未声明"
    ]
    forbidden = [
        f"{row['ecosystem']}:{row['name']}@{row['version']} ({row['license']})"
        for row in rows
        if FORBIDDEN_LICENSE.search(row["license"])
    ]
    policy = {"passed": not unknown and not forbidden, "unknown": unknown, "forbidden": forbidden}
    (output / "LICENSE_POLICY.json").write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"collected {len(rows)} runtime dependency records into {output}")
    if args.fail_on_policy and not policy["passed"]:
        raise SystemExit(4)


if __name__ == "__main__":
    main()
