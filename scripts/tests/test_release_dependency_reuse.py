"""Synthetic checks for dependency reuse; no product or installer is built."""

import importlib.util
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/release_dependency_reuse.py"
FRONTEND = ROOT / "scripts/release_frontend_reuse.mjs"
SPEC = importlib.util.spec_from_file_location("release_reuse_test", HELPER)
assert SPEC and SPEC.loader
reuse = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reuse)


@pytest.fixture
def isolated_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    candidate = workspace / "candidate"
    candidate.mkdir(parents=True)
    (workspace / "node_modules").mkdir()
    for name in reuse.LOCKED_INPUTS:
        for root in (workspace, candidate):
            file = root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(f"synthetic {name}\n", encoding="utf-8")
    return workspace, candidate


@pytest.mark.parametrize("changed", reuse.LOCKED_INPUTS)
def test_preflight_rejects_each_candidate_lock_or_manifest_mismatch(isolated_workspace, changed):
    workspace, candidate = isolated_workspace
    (candidate / changed).write_text("different\n", encoding="utf-8")
    with pytest.raises(ValueError, match="differ"):
        reuse.workspace_inputs(candidate, workspace)


def test_preflight_rejects_candidate_node_modules_and_external_candidate(isolated_workspace):
    workspace, candidate = isolated_workspace
    (candidate / "node_modules").mkdir()
    with pytest.raises(ValueError, match="node_modules"):
        reuse.workspace_inputs(candidate, workspace)
    with pytest.raises(ValueError, match="beneath"):
        reuse.workspace_inputs(workspace.parent, workspace)


def test_preflight_rejects_intermediate_package_metadata(isolated_workspace):
    workspace, original = isolated_workspace
    candidate = workspace / "intermediate" / "candidate"
    candidate.parent.mkdir()
    original.rename(candidate)
    (candidate.parent / "package.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="intermediate package.json"):
        reuse.workspace_inputs(candidate, workspace)


def test_node_inventory_rejects_missing_or_wrong_build_dependency(isolated_workspace):
    workspace, candidate = isolated_workspace
    lock = {"lockfileVersion": 3, "packages": {
        "node_modules/tool": {"version": "1.0.0", "dev": True},
        "node_modules/other-platform": {"version": "1.0.0", "optional": True, "os": ["never-a-real-platform"]},
    }}
    (candidate / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        reuse.node_inventory(candidate, workspace)
    manifest = workspace / "node_modules/tool/package.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"name": "tool", "version": "2.0.0"}), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        reuse.node_inventory(candidate, workspace)
    manifest.write_text(json.dumps({"name": "tool", "version": "1.0.0"}), encoding="utf-8")
    result = reuse.node_inventory(candidate, workspace)
    assert result["installed_packages"] == 1
    assert result["incompatible_optional_packages_skipped"] == 1


def test_python_runtime_inventory_rejects_mismatched_or_unpinned_requirements(tmp_path, monkeypatch):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("example==1.2.3\n", encoding="utf-8")
    monkeypatch.setattr(reuse.metadata, "version", lambda _name: "1.2.3")
    assert reuse.python_runtime_inventory(requirements)["installed_runtime_packages"] == 1
    monkeypatch.setattr(reuse.metadata, "version", lambda _name: "1.2.4")
    with pytest.raises(ValueError, match="differs from frozen lock"):
        reuse.python_runtime_inventory(requirements)
    requirements.write_text("example>=1.2.3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not exactly pinned"):
        reuse.python_runtime_inventory(requirements)


def test_actual_frozen_python_lock_matches_existing_venv_without_sync(tmp_path):
    requirements = tmp_path / "requirements.txt"
    env = os.environ.copy()
    env["UV_NO_SYNC"] = "1"
    result = subprocess.run([
        "uv", "export", "--cache-dir", str(tmp_path / "uv-cache"), "--project", str(ROOT / "apps/api"),
        "--frozen", "--no-dev", "--no-emit-project", "--no-header", "--format", "requirements-txt",
        "--output-file", str(requirements),
    ], cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert reuse.python_runtime_inventory(requirements)["installed_runtime_packages"] > 0


def test_pyinstaller_analysis_rejects_main_tree_application_source(isolated_workspace):
    workspace, candidate = isolated_workspace
    candidate_app = candidate / "apps/api/src/document_pipeline_api"
    main_app = workspace / "apps/api/src/document_pipeline_api"
    candidate_app.mkdir(parents=True)
    main_app.mkdir(parents=True)
    (candidate_app / "__init__.py").write_text("# candidate\n")
    (main_app / "__init__.py").write_text("# main\n")
    toc = candidate / "Analysis-00.toc"
    toc.write_text(repr(([('document_pipeline_api', str(candidate_app / '__init__.py'), 'PYMODULE')],)))
    assert reuse.verify_pyinstaller_analysis(candidate, workspace, toc)["application_modules"] == 1
    toc.write_text(repr(([('document_pipeline_api', str(main_app / '__init__.py'), 'PYMODULE')],)))
    with pytest.raises(ValueError, match="outside candidate"):
        reuse.verify_pyinstaller_analysis(candidate, workspace, toc)


def copy_real_input_manifests(candidate):
    for name in reuse.LOCKED_INPUTS:
        destination = candidate / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    module = candidate / "apps/api/src/document_pipeline_api/__init__.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text("# isolated synthetic application package\n", encoding="utf-8")


def test_real_venv_preflight_uses_candidate_python_source_and_existing_tools(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    copy_real_input_manifests(candidate)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(candidate / "apps/api/src")
    env["PYTHONNOUSERSITE"] = "1"
    command = [sys.executable, str(HELPER), "preflight", "--candidate", str(candidate), "--dependency-root", str(ROOT)]
    good = subprocess.run(command, cwd=candidate, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert good.returncode == 0, good.stderr
    result = json.loads(good.stdout)
    assert result["node_tool_versions"]["typescript"]
    assert result["node_tool_versions"]["vite"]
    assert result["python_application_source"] == "candidate/apps/api/src/document_pipeline_api"
    env["PYTHONPATH"] = str(ROOT / "apps/api/src")
    wrong = subprocess.run(command, cwd=candidate, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert wrong.returncode != 0
    assert "outside the candidate" in wrong.stderr
    shadow = tmp_path / "shadow-tools"
    shadow.mkdir()
    (shadow / "pip_audit.py").write_text("# synthetic shadow\n", encoding="utf-8")
    env["PYTHONPATH"] = str(shadow) + os.pathsep + str(candidate / "apps/api/src")
    wrong_tool = subprocess.run(command, cwd=candidate, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert wrong_tool.returncode != 0
    assert "outside the workspace virtual environment" in wrong_tool.stderr
    assert not (candidate / "node_modules").exists()


@pytest.fixture
def frontend_candidate(tmp_path):
    candidate = tmp_path / "candidate"
    web = candidate / "apps/web"
    source = web / "src"
    source.mkdir(parents=True)
    (web / "tsconfig.json").write_text(json.dumps({
        "compilerOptions": {"target": "ES2022", "noEmit": True, "types": []},
        "include": ["src/typecheck.ts"],
    }), encoding="utf-8")
    (web / "vite.config.ts").write_text("export default {};\n", encoding="utf-8")
    (web / "index.html").write_text('<script type="module" src="/src/main.js"></script>\n', encoding="utf-8")
    (source / "typecheck.ts").write_text("export const typecheck = true;\n", encoding="utf-8")
    (source / "main.js").write_text("document.body.textContent = 'synthetic';\n", encoding="utf-8")
    return candidate


def run_frontend(candidate):
    report = candidate / ".local/frontend-graph.json"
    env = os.environ.copy()
    env.pop("NODE_OPTIONS", None)
    env.pop("NODE_PATH", None)
    result = subprocess.run([
        "node", str(FRONTEND), "--candidate", str(candidate), "--dependency-root", str(ROOT),
        "--report", str(report),
    ], cwd=candidate / "apps/web", env=env, capture_output=True, text=True, encoding="utf-8", timeout=45)
    return result, report


def test_real_typescript_and_vite_entries_build_only_synthetic_candidate(frontend_candidate):
    result, report = run_frontend(frontend_candidate)
    assert result.returncode == 0, result.stdout + result.stderr
    graph = json.loads(report.read_text(encoding="utf-8"))
    assert graph["typescript_files"] > 0 and graph["vite_files"] > 0
    assert any(item["path"] == "candidate-web/src/main.js" for item in graph["files"])
    assert str(ROOT).replace("\\", "/") not in report.read_text(encoding="utf-8").replace("\\", "/")
    assert (frontend_candidate / "apps/web/dist/index.html").is_file()
    assert not (frontend_candidate / "node_modules").exists()


@pytest.mark.parametrize("incoming_node_env", [None, "development"])
def test_real_vite_react_plugin_and_config_load_from_existing_dependencies(frontend_candidate, monkeypatch, incoming_node_env):
    if incoming_node_env is None:
        monkeypatch.delenv("NODE_ENV", raising=False)
    else:
        monkeypatch.setenv("NODE_ENV", incoming_node_env)
    web = frontend_candidate / "apps/web"
    (web / "vite.config.ts").write_text(
        'import react from "@vitejs/plugin-react"; import { defineConfig } from "vitest/config"; '
        'import { loadEnv } from "vite"; export default defineConfig(({mode}) => '
        '({plugins: [react()], define: {"__FIXTURE__": JSON.stringify(loadEnv(mode,".","FIXTURE_").OK || "")} }));\n',
        encoding="utf-8",
    )
    (web / "tsconfig.app.json").write_text(json.dumps({
        "compilerOptions": {"target": "ES2022", "module": "ESNext", "moduleResolution": "Bundler",
                            "jsx": "react-jsx", "noEmit": True, "skipLibCheck": True, "types": []},
        "include": ["src/main.tsx"],
    }), encoding="utf-8")
    (web / "tsconfig.node.json").write_text(json.dumps({
        "compilerOptions": {"composite": True, "skipLibCheck": True, "module": "ESNext", "moduleResolution": "Bundler"},
        "include": ["vite.config.ts"],
    }), encoding="utf-8")
    (web / "tsconfig.json").write_text(json.dumps({"files": [], "references": [
        {"path": "./tsconfig.app.json"}, {"path": "./tsconfig.node.json"}
    ]}), encoding="utf-8")
    (web / "index.html").write_text('<div id="root"></div><script type="module" src="/src/main.tsx"></script>', encoding="utf-8")
    (web / "src/main.tsx").write_text(
        'import { createRoot } from "react-dom/client"; createRoot(document.getElementById("root")!).render(<p>fixture</p>);\n',
        encoding="utf-8",
    )
    result, report = run_frontend(frontend_candidate)
    assert result.returncode == 0, result.stdout + result.stderr
    files = json.loads(report.read_text(encoding="utf-8"))["files"]
    paths = [entry["path"] for entry in files]
    assert any(path.startswith("workspace-node-modules/react-dom/") for path in paths)
    assert "candidate-web/src/main.tsx" in paths
    bundled = [entry["path"] for entry in files if entry["stage"] in {"vite", "vite-output"}]
    assert "workspace-node-modules/react/cjs/react.production.js" in bundled
    assert "workspace-node-modules/react-dom/cjs/react-dom-client.production.js" in bundled
    assert not any(".development.js" in path for path in bundled if "/react/" in path or "/react-dom/" in path)
    assert not any("jsx-dev-runtime" in path for path in bundled if "/react/" in path)


@pytest.mark.parametrize("mutation", ["node_env", "mode"])
def test_vite_plugins_cannot_switch_release_back_to_development(frontend_candidate, mutation):
    web = frontend_candidate / "apps/web"
    change = 'process.env.NODE_ENV = "development";' if mutation == "node_env" else 'return {mode: "development"};'
    (web / "vite.config.ts").write_text(
        'export default {plugins: [{name: "synthetic-mode-change", config() {' + change + '}}]};\n', encoding="utf-8"
    )
    if mutation == "mode":
        (web / "src/main.js").write_text('document.body.dataset.mode = import.meta.env.MODE;\n', encoding="utf-8")
    result, report = run_frontend(frontend_candidate)
    if mutation == "node_env":
        assert result.returncode != 0
        assert "production" in result.stderr.lower()
        assert not report.exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert report.is_file()
        bundles = list((web / "dist/assets").glob("*.js"))
        assert len(bundles) == 1
        output = bundles[0].read_text(encoding="utf-8")
        assert "dataset.mode=" in output and "production" in output and "development" not in output


def test_development_react_runtime_cannot_enter_release_graph(frontend_candidate):
    web = frontend_candidate / "apps/web"
    development = ROOT / "node_modules/react/cjs/react.development.js"
    (web / "vite.config.ts").write_text(
        'export default {resolve: {alias: {"synthetic-dev-react": ' + json.dumps(str(development)) + '}}};\n', encoding="utf-8"
    )
    (web / "src/main.js").write_text('import React from "synthetic-dev-react"; console.log(React.version);\n', encoding="utf-8")
    result, report = run_frontend(frontend_candidate)
    assert result.returncode != 0
    assert "development" in result.stderr.lower()
    assert not report.exists()


def test_typescript_project_references_work_with_checked_no_emit(frontend_candidate):
    web = frontend_candidate / "apps/web"
    app = json.loads((web / "tsconfig.json").read_text(encoding="utf-8"))
    (web / "tsconfig.app.json").write_text(json.dumps(app), encoding="utf-8")
    (web / "tsconfig.node.json").write_text(json.dumps({
        "compilerOptions": {"composite": True, "skipLibCheck": True, "types": []},
        "include": ["vite.config.ts"],
    }), encoding="utf-8")
    (web / "tsconfig.json").write_text(json.dumps({"files": [], "references": [
        {"path": "./tsconfig.app.json"}, {"path": "./tsconfig.node.json"}
    ]}), encoding="utf-8")
    result, report = run_frontend(frontend_candidate)
    assert result.returncode == 0, result.stdout + result.stderr
    assert report.is_file()


def test_vite_worker_inside_candidate_is_recorded(frontend_candidate):
    web = frontend_candidate / "apps/web"
    (web / "src/worker.js").write_text('postMessage("fixture");\n', encoding="utf-8")
    (web / "src/main.js").write_text('new Worker(new URL("./worker.js", import.meta.url), {type:"module"});\n', encoding="utf-8")
    result, report = run_frontend(frontend_candidate)
    assert result.returncode == 0, result.stdout + result.stderr
    files = json.loads(report.read_text(encoding="utf-8"))["files"]
    assert any(item["path"] == "candidate-web/src/worker.js" for item in files)


@pytest.mark.parametrize("stage", ["typescript", "vite", "vite_config", "vite_asset", "vite_css_asset", "vite_worker"])
def test_real_tool_graph_rejects_application_source_outside_candidate_web(frontend_candidate, stage):
    web = frontend_candidate / "apps/web"
    if stage == "typescript":
        (frontend_candidate / "apps/outside.ts").write_text("export const outside = true;\n", encoding="utf-8")
        (web / "src/typecheck.ts").write_text('import { outside } from "../../outside"; export { outside };\n', encoding="utf-8")
    elif stage == "vite":
        (frontend_candidate / "apps/outside.js").write_text("export const outside = true;\n", encoding="utf-8")
        (web / "src/main.js").write_text('import { outside } from "../../outside.js"; console.log(outside);\n', encoding="utf-8")
    elif stage == "vite_config":
        (frontend_candidate / "apps/outside.ts").write_text('export const text = "unsafe";\n', encoding="utf-8")
        (web / "vite.config.ts").write_text('import { text } from "../outside.ts"; export default { define: { text } };\n', encoding="utf-8")
    elif stage == "vite_asset":
        (frontend_candidate / "apps/outside.png").write_bytes(b"synthetic image")
        (web / "index.html").write_text('<img src="../outside.png"><script type="module" src="/src/main.js"></script>', encoding="utf-8")
    elif stage == "vite_css_asset":
        (frontend_candidate / "apps/outside.png").write_bytes(b"synthetic image")
        (web / "src/style.css").write_text('body { background-image: url("../../outside.png"); }\n', encoding="utf-8")
        (web / "src/main.js").write_text('import "./style.css";\n', encoding="utf-8")
    else:
        (frontend_candidate / "apps/outside.js").write_text('postMessage("outside");\n', encoding="utf-8")
        (web / "src/main.js").write_text('new Worker(new URL("../../outside.js", import.meta.url), {type:"module"});\n', encoding="utf-8")
    result, report = run_frontend(frontend_candidate)
    assert result.returncode != 0
    assert "outside candidate" in result.stderr
    assert not report.exists()


@pytest.mark.parametrize("stage", ["typescript", "vite_config"])
def test_real_frontend_tools_must_not_write_outside_candidate_web(frontend_candidate, stage):
    web = frontend_candidate / "apps/web"
    target = frontend_candidate / "apps/forbidden-output.txt"
    if stage == "typescript":
        config = json.loads((web / "tsconfig.json").read_text(encoding="utf-8"))
        config["compilerOptions"].update({"incremental": True, "tsBuildInfoFile": str(target)})
        (web / "tsconfig.json").write_text(json.dumps(config), encoding="utf-8")
    else:
        (web / "vite.config.ts").write_text(
            'import { writeFileSync } from "node:fs"; writeFileSync('
            + json.dumps(str(target)) + ', "must not be written"); export default {};\n', encoding="utf-8")
    result, report = run_frontend(frontend_candidate)
    assert result.returncode != 0
    assert not target.exists()
    assert not report.exists()


def test_vite_plugin_cannot_redirect_native_bundle_output(frontend_candidate):
    web = frontend_candidate / "apps/web"
    target = frontend_candidate / "apps/forbidden-dist"
    (web / "vite.config.ts").write_text(
        'export default { plugins: [{ name: "fixture", config() { return { build: { outDir: '
        + json.dumps(str(target)) + ' } }; } }] };\n', encoding="utf-8")
    result, report = run_frontend(frontend_candidate)
    assert result.returncode != 0
    assert not target.exists()
    assert not report.exists()


@pytest.mark.parametrize("kind", ["typescript_extends", "vite_public_dir"])
def test_standard_config_cannot_include_source_outside_candidate_web(frontend_candidate, kind):
    web = frontend_candidate / "apps/web"
    if kind == "typescript_extends":
        external = frontend_candidate / "apps/base.json"
        external.write_text('{"compilerOptions":{"target":"ES2022","noEmit":true,"types":[]}}', encoding="utf-8")
        (web / "tsconfig.json").write_text('{"extends":"../base.json","include":["src/typecheck.ts"]}', encoding="utf-8")
    else:
        external = frontend_candidate / "apps/outside-public"
        external.mkdir()
        (external / "outside.txt").write_text("outside source\n", encoding="utf-8")
        (web / "vite.config.ts").write_text(
            'export default { publicDir: ' + json.dumps(str(external)) + ' };\n', encoding="utf-8")
    result, report = run_frontend(frontend_candidate)
    assert result.returncode != 0
    assert not report.exists()
    assert not (web / "dist/outside.txt").exists()


def test_local_extended_config_and_unimported_public_asset_are_bound(frontend_candidate):
    web = frontend_candidate / "apps/web"
    (web / "tsconfig.base.json").write_text(
        '{ // supported JSONC\n "compilerOptions": {"target":"ES2022","noEmit":true,"types":[],}, }\n',
        encoding="utf-8",
    )
    (web / "tsconfig.json").write_text('{"extends":"./tsconfig.base.json","include":["src/typecheck.ts"]}', encoding="utf-8")
    (web / "public").mkdir()
    (web / "public/unimported.txt").write_text("public fixture\n", encoding="utf-8")
    result, report = run_frontend(frontend_candidate)
    assert result.returncode == 0, result.stdout + result.stderr
    files = json.loads(report.read_text(encoding="utf-8"))["files"]
    assert any(item["path"] == "candidate-web/tsconfig.base.json" for item in files)
    assert any(item["path"] == "candidate-web/public/unimported.txt" for item in files)
    assert (web / "dist/unimported.txt").read_text(encoding="utf-8") == "public fixture\n"


def test_ambiguous_typescript_extends_file_and_directory_is_rejected(frontend_candidate):
    web = frontend_candidate / "apps/web"
    (web / "base").mkdir()
    (web / "base/tsconfig.json").write_text('{"compilerOptions":{"target":"ES2022"}}', encoding="utf-8")
    (web / "base.json").write_text('{"extends":"../outside.json"}', encoding="utf-8")
    (frontend_candidate / "apps/outside.json").write_text('{"compilerOptions":{"target":"ES2022"}}', encoding="utf-8")
    (web / "tsconfig.json").write_text('{"extends":"./base","include":["src/typecheck.ts"]}', encoding="utf-8")
    result, report = run_frontend(frontend_candidate)
    assert result.returncode != 0
    assert "ambiguous" in result.stderr.lower()
    assert not report.exists()


@pytest.fixture
def powershell_release_candidate(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    copy_real_input_manifests(candidate)
    for path in ("VERSION", "apps/api/src/document_pipeline_api/version.py"):
        shutil.copyfile(ROOT / path, candidate / path)
    (candidate / "apps/api/alembic.ini").write_text("[alembic]\nscript_location = %(here)s/migrations\n", encoding="utf-8")
    versions = candidate / "apps/api/migrations/versions"
    versions.mkdir(parents=True)
    (versions / "fixture.py").write_text('revision = "fixture"\ndown_revision = None\nbranch_labels = None\ndepends_on = None\n', encoding="utf-8")
    (candidate / ".gitignore").write_text(".local/\n.npm-cache/\n.uv-cache/\n__pycache__/\n", encoding="utf-8")
    for name in ["build-release.ps1", "release_dependency_reuse.py", "release_evidence.py"]:
        dst = candidate / "scripts" / name
        dst.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "scripts" / name, dst)
    def git(*args):
        return subprocess.run(["git", "-C", str(candidate), *args], check=True, capture_output=True, text=True)
    git("init", "--quiet")
    git("add", ".")
    git("-c", "user.name=Release fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    for name in ("node", "npm"):
        (fake_bin / f"{name}.cmd").write_text(
            '@echo off\r\nif "%~1"=="--version" (echo 99.0.0 & exit /b 0)\r\n'
            f'>>"%REUSE_TEST_MARKER%" echo {name}-operation\r\nexit /b 47\r\n', encoding="ascii")
    (fake_bin / "uv.cmd").write_text(
        '@echo off\r\nif "%~1"=="--version" (echo 99.0.0 & exit /b 0)\r\n'
        'if "%~1"=="export" goto export\r\n'
        '>>"%REUSE_TEST_MARKER%" echo uv-operation\r\nexit /b 47\r\n'
        ':export\r\n>>"%REUSE_TEST_MARKER%" echo uv-export\r\n'
        ':next\r\nif "%~1"=="" exit /b 48\r\n'
        'if "%~1"=="--output-file" ( >"%~2" echo packaging==%REUSE_TEST_PACKAGING_VERSION% & exit /b 0 )\r\n'
        'shift\r\ngoto next\r\n', encoding="ascii")
    marker = tmp_path / "commands.txt"
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    env["REUSE_TEST_MARKER"] = str(marker)
    env["REUSE_TEST_PACKAGING_VERSION"] = metadata.version("packaging")
    return candidate, marker, env


def test_powershell_reuse_does_not_run_install_or_sync_and_restores_environment(powershell_release_candidate, tmp_path):
    candidate, marker, env = powershell_release_candidate
    wrapper = tmp_path / "wrapper.ps1"
    wrapper.write_text(r'''param([string]$Script, [string]$Workspace, [string]$Output)
$env:PYTHONPATH = "sentinel-python-path"
$env:NODE_OPTIONS = "sentinel-node-options"
$env:UV_NO_SYNC = "sentinel-uv"
$env:PYINSTALLER_CONFIG_DIR = "sentinel-pyinstaller"
try {
    & $Script -ReuseWorkspaceDependencies -WorkspaceDependencyRoot $Workspace -OutputDirectory $Output
    throw "Expected the synthetic node command to stop the fixture"
} catch { Write-Output ("expected-error: " + $_.Exception.Message) }
if ($env:PYTHONPATH -ne "sentinel-python-path" -or $env:NODE_OPTIONS -ne "sentinel-node-options" -or $env:UV_NO_SYNC -ne "sentinel-uv" -or $env:PYINSTALLER_CONFIG_DIR -ne "sentinel-pyinstaller") { exit 9 }
Write-Output "environment-restored"
''', encoding="utf-8")
    output = candidate / ".local/release-fixture"
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(wrapper),
        "-Script", str(candidate / "scripts/build-release.ps1"), "-Workspace", str(ROOT), "-Output", str(output)],
        cwd=candidate, env=env, capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "environment-restored" in result.stdout
    assert "Frontend reuse build or source graph validation failed" in result.stdout
    assert marker.read_text().splitlines() == ["uv-export", "node-operation"]
    assert (output / "audit/WORKSPACE_REUSE_PRIVATE.json").is_file()
    assert not (candidate / "node_modules").exists()
    assert not (output / "dist/installer").exists()


def test_powershell_reuse_arguments_must_be_explicit_before_any_tool_runs(powershell_release_candidate):
    candidate, marker, env = powershell_release_candidate
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(candidate / "scripts/build-release.ps1"),
        "-ReuseWorkspaceDependencies"], cwd=candidate, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode != 0
    assert "requires both" in result.stderr
    assert not marker.exists()


def test_powershell_default_still_invokes_existing_uv_path(powershell_release_candidate):
    candidate, marker, env = powershell_release_candidate
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(candidate / "scripts/build-release.ps1")],
        cwd=candidate, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode != 0
    assert marker.read_text().splitlines() == ["uv-operation"]
    assert "Cannot resolve the current alembic migration head" in result.stderr
