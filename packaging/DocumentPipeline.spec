from pathlib import Path
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
APP_VERSION = os.environ.get("DOCUMENT_PIPELINE_BUILD_VERSION", "0.0.0")
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
    (str(API / "alembic.ini"), "api_runtime"),
    (str(API / "migrations"), "api_runtime/migrations"),
]
if RELEASE_RESOURCES.is_dir():
    datas.append((str(RELEASE_RESOURCES), "licenses"))
datas.extend(pdf_datas)

analysis = Analysis(
    [str(API / "src" / "document_pipeline_api" / "launcher.py")],
    pathex=[str(API / "src")],
    binaries=pdf_binaries,
    datas=datas,
    hiddenimports=[
        *pdf_hiddenimports,
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
    upx=True,
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
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name="Zhiyi",
)
