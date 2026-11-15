from pathlib import Path
import ast
import os

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


ROOT = Path(SPECPATH).parent.resolve()
API = ROOT / "apps" / "api"
WEB = ROOT / "apps" / "web" / "dist"
ICON = ROOT / "packaging" / "app.ico"
RELEASE_RESOURCES = Path(
    os.environ.get(
        "DOCUMENT_PIPELINE_RELEASE_RESOURCES",
        ROOT / ".local" / "release-resources",
    )
).resolve()
APP_VERSION = os.environ.get("DOCUMENT_PIPELINE_BUILD_VERSION", "").strip()
if not APP_VERSION:
    raise ValueError("DOCUMENT_PIPELINE_BUILD_VERSION is required; use scripts/build-release.ps1.")
version_parts = [int(part) for part in APP_VERSION.split(".")]
if len(version_parts) > 4 or not version_parts:
    raise ValueError(f"Unsupported Windows version: {APP_VERSION}")
version_tuple = tuple((version_parts + [0] * 4)[:4])
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=version_tuple,
        prodvers=version_tuple,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "080404B0",
                    [
                        StringStruct("CompanyName", "知意"),
                        StringStruct("FileDescription", "知意"),
                        StringStruct("FileVersion", APP_VERSION),
                        StringStruct("InternalName", "Zhiyi"),
                        StringStruct("OriginalFilename", "Zhiyi.exe"),
                        StringStruct("ProductName", "知意"),
                        StringStruct("ProductVersion", APP_VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [2052, 1200])]),
    ],
)

pdf_datas, pdf_binaries, pdf_hiddenimports = collect_all("pypdfium2")
datas = [
    (str(WEB), "web"),
    (str(ICON), "desktop"),
    (str(ROOT / "packaging" / "install-update.ps1"), "desktop"),
    (str(API / "alembic.ini"), "api_runtime"),
]
# Resolving the migration head creates local bytecode caches. Ship the source
# migrations and Alembic template only, never generated .pyc files with build paths.
for migration_asset in sorted((API / "migrations").rglob("*")):
    if migration_asset.is_file() and migration_asset.suffix in {".py", ".mako"}:
        destination = Path("api_runtime/migrations") / migration_asset.relative_to(API / "migrations").parent
        datas.append((str(migration_asset), str(destination)))
if RELEASE_RESOURCES.is_dir():
    datas.append((str(RELEASE_RESOURCES), "licenses"))
datas.extend(pdf_datas)

# Alembic loads migration files as data, outside PyInstaller's import graph.
# Include their application imports so a clean installation and an old-database
# upgrade have the same dependencies as a source-tree run.
migration_imports = set()
for migration in (API / "migrations").rglob("*.py"):
    for node in ast.walk(ast.parse(migration.read_text(encoding="utf-8-sig"))):
        names = [node.module] if isinstance(node, ast.ImportFrom) else (
            [name.name for name in node.names] if isinstance(node, ast.Import) else []
        )
        migration_imports.update(name for name in names if name and name.startswith("document_pipeline_api."))

analysis = Analysis(
    [str(API / "src" / "document_pipeline_api" / "launcher.py")],
    pathex=[str(API / "src")],
    binaries=pdf_binaries,
    datas=datas,
    hiddenimports=[
        *pdf_hiddenimports,
        *sorted(migration_imports),
        "document_pipeline_api.main",
        "document_pipeline_api.desktop",
        "document_pipeline_api.mcp_server",
        "document_pipeline_api.worker",
        "webview",
        "webview.platforms.edgechromium",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Shelved experiments stay in the repository but must never enter a release.
    excludes=["document_pipeline_api.services.builtin_model"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

gui_exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Zhiyi",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
    version=version_info,
)

cli_exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ZhiyiCLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
    version=version_info,
)

collect = COLLECT(
    gui_exe,
    cli_exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Zhiyi",
)
