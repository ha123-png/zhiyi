"""Release reuse must never silently omit a production package or its license."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "collect-third-party-notices.py"
ROOT = SCRIPT.parents[1]
SPEC = importlib.util.spec_from_file_location("reuse_license_collector", SCRIPT)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


@pytest.fixture
def workspace(tmp_path):
    candidate = tmp_path / "candidate"
    dependencies = tmp_path / "dependencies"
    output = tmp_path / "output"
    candidate.mkdir()
    dependencies.mkdir()
    output.mkdir()
    package = dependencies / "node_modules" / "example-package"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"name": "example-package", "version": "1.2.3", "license": "MIT"}),
        encoding="utf-8",
    )
    (package / "LICENSE").write_text("Example license text\n", encoding="utf-8")
    lock = {
        "packages": {
            "": {"name": "candidate"},
            "node_modules/example-package": {"version": "1.2.3", "license": "MIT"},
            "node_modules/other-platform": {
                "version": "1.0.0", "optional": True, "os": ["not-this-platform"]
            },
        }
    }
    (candidate / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    return candidate, dependencies, output, package, lock


def test_reused_dependency_supplies_the_license_with_no_candidate_node_modules(workspace):
    candidate, dependencies, output, _, _ = workspace
    rows = collector._copy_node_licenses(candidate, output, dependency_root=dependencies)
    assert rows == [{"ecosystem": "node", "name": "example-package", "version": "1.2.3", "license": "MIT"}]
    assert (output / "node/example-package-1.2.3-LICENSE").read_text() == "Example license text\n"
    assert not (candidate / "node_modules").exists()


@pytest.mark.parametrize("fault", ["missing_package", "missing_manifest", "wrong_version", "missing_license"])
def test_reuse_rejects_missing_or_mismatched_production_package(workspace, fault):
    candidate, dependencies, output, package, _ = workspace
    if fault == "missing_package":
        (package / "package.json").unlink()
        (package / "LICENSE").unlink()
        package.rmdir()
    elif fault == "missing_manifest":
        (package / "package.json").unlink()
    elif fault == "wrong_version":
        (package / "package.json").write_text(json.dumps({"version": "9.9.9", "license": "MIT"}))
    else:
        (package / "LICENSE").unlink()
    with pytest.raises(RuntimeError):
        collector._copy_node_licenses(candidate, output, dependency_root=dependencies)


def test_applicable_optional_package_must_not_disappear(workspace):
    candidate, dependencies, output, _, lock = workspace
    lock["packages"]["node_modules/other-platform"].pop("os")
    (candidate / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(RuntimeError):
        collector._copy_node_licenses(candidate, output, dependency_root=dependencies)


def test_default_clean_build_uses_its_own_packages_and_rejects_missing_license(workspace):
    candidate, dependencies, output, package, lock = workspace
    (dependencies / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    rows = collector._copy_node_licenses(dependencies, output)
    assert [row["name"] for row in rows] == ["example-package"]
    assert (output / "node/example-package-1.2.3-LICENSE").is_file()
    assert not (candidate / "node_modules").exists()
    (package / "LICENSE").unlink()
    with pytest.raises(RuntimeError, match="no license file"):
        collector._copy_node_licenses(dependencies, output / "missing")


def test_exact_reviewed_exception_is_disclosed_and_rejects_changed_package(workspace, monkeypatch):
    candidate, dependencies, output, package, lock = workspace
    (package / "LICENSE").unlink()
    lock["packages"]["node_modules/example-package"]["integrity"] = "sha512-reviewed"
    (candidate / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    licenses = candidate / "packaging/licenses"
    licenses.mkdir(parents=True)
    license_file = licenses / "upstream-LICENSE.txt"
    license_file.write_text("Exact reviewed upstream text\n", encoding="utf-8")
    record = {
        "version": "1.2.3", "package_integrity": "sha512-reviewed",
        "package_tree_sha256": collector._package_tree_sha256(package),
        "file": license_file.name, "sha256": hashlib.sha256(license_file.read_bytes()).hexdigest(),
        "license": "MIT", "source_url": "https://example.test/upstream/LICENSE",
        "source_revision": "exact-source", "disclosure": "Upstream did not ship the text in its package.",
        "nested_license_sha256": {},
    }
    (licenses / "node-overrides.json").write_text(json.dumps({
        "schema": 1, "packages": {"example-package@1.2.3": record}
    }), encoding="utf-8")
    monkeypatch.setattr(collector, "_runtime_python_versions", lambda *_: {})
    monkeypatch.setattr(collector, "_copy_python_licenses", lambda *_: [])
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--root", str(candidate), "--output", str(output),
                                      "--node-dependency-root", str(dependencies), "--version", "0.0.0",
                                      "--revision", "fixture", "--python-requirements", "unused",
                                      "--python-audit", "unused", "--fail-on-policy"])
    collector.main()
    notices = (output / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert record["disclosure"] in notices and record["source_url"] in notices
    assert (output / "node/example-package-1.2.3-upstream-LICENSE.txt").is_file()
    license_file.write_text("changed license\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="upstream license differs"):
        collector._copy_node_licenses(candidate, output / "changed-license", dependency_root=dependencies)
    license_file.write_text("Exact reviewed upstream text\n", encoding="utf-8")
    lock["packages"]["node_modules/example-package"]["integrity"] = "sha512-other"
    (candidate / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(RuntimeError, match="exact package bytes"):
        collector._copy_node_licenses(candidate, output / "changed-integrity", dependency_root=dependencies)
    lock["packages"]["node_modules/example-package"]["integrity"] = "sha512-reviewed"
    (candidate / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    (package / "new-file.js").write_text("changed package content\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="exact package bytes"):
        collector._copy_node_licenses(candidate, output / "second", dependency_root=dependencies)


@pytest.mark.parametrize("explicit_dependency_root", [False, True])
def test_real_workspace_exact_exceptions_include_vendored_notices(tmp_path, explicit_dependency_root):
    rows = collector._copy_node_licenses(ROOT, tmp_path, dependency_root=ROOT if explicit_dependency_root else None)
    by_name = {row["name"]: row for row in rows}
    assert by_name["victory-vendor"]["license"] == "MIT AND ISC AND BSD-3-Clause"
    assert "上游未附许可证全文或版权声明" in by_name["use-composed-ref"]["license_provenance"]["disclosure"]
    assert "不是该 tarball 原件" in by_name["react-remove-scroll-bar"]["license_provenance"]["disclosure"]
    nested = list((tmp_path / "node").glob("victory-vendor-37.3.6-lib-vendor_*_LICENSE"))
    assert len(nested) == 13
