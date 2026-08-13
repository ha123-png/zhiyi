import argparse
from importlib import metadata
import json
from pathlib import Path
import re
import shutil
import tomllib
from packaging.markers import Marker
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


def _runtime_python_names(root: Path) -> set[str]:
    lock = tomllib.loads((root / "apps" / "api" / "uv.lock").read_text(encoding="utf-8"))
    packages = {_normalized(item["name"]): item for item in lock["package"]}
    project = packages["document-pipeline-api"]
    def applies(item: dict[str, str]) -> bool:
        marker = item.get("marker")
        return marker is None or Marker(marker).evaluate()

    pending = [
        _normalized(item["name"])
        for item in project.get("dependencies", [])
        if applies(item)
    ]
    selected: set[str] = set()
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected.add(name)
        package = packages.get(name)
        if package:
            pending.extend(
                _normalized(item["name"])
                for item in package.get("dependencies", [])
                if applies(item)
            )
    return selected


def _copy_python_licenses(
    output: Path, selected_names: set[str]
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    distributions = {
        _normalized(item.metadata.get("Name") or item.name): item
        for item in metadata.distributions()
    }
    missing = sorted(selected_names - distributions.keys())
    if missing:
        raise RuntimeError(f"Python runtime dependencies are not installed: {missing}")
    for normalized_name in sorted(selected_names):
        distribution = distributions[normalized_name]
        name = distribution.metadata.get("Name") or distribution.name
        rows.append(
            {
                "ecosystem": "python",
                "name": name,
                "version": distribution.version,
                "license": _license_for_distribution(distribution),
            }
        )
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


def _copy_build_tool_licenses(root: Path, output: Path) -> list[dict[str, str]]:
    nsis_license = root / ".local" / "tools" / "nsis" / "Docs" / "AppendixI.html"
    if not nsis_license.is_file():
        return []
    destination = output / "build-tools" / "NSIS-3.12-License.html"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(nsis_license, destination)
    return [
        {
            "ecosystem": "build-tool",
            "name": "NSIS",
            "version": "3.12",
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
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    rows = _copy_python_licenses(output, _runtime_python_names(root))
    rows.extend(_copy_node_licenses(root, output))
    rows.extend(_copy_build_tool_licenses(root, output))
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
