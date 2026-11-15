"""Check the silent-uninstall decision without running the product installer.

The NSIS fixture can only delete a synthetic marker in its own temporary
directory. It loads the same option parser as the real uninstaller.
"""

import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
_program_files = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
_default_nsis = Path(_program_files) / "NSIS" / "makensis.exe"
MAKENSIS = shutil.which("makensis") or (str(_default_nsis) if _default_nsis.is_file() else None)
pytestmark = pytest.mark.skipif(os.name != "nt" or not MAKENSIS, reason="Windows NSIS is required")


def _nsis_path(path: Path) -> str:
    return str(path).replace("$", "$$")


@pytest.fixture(scope="module")
def probe_uninstaller(tmp_path_factory):
    root = tmp_path_factory.mktemp("purge-probe")
    script = root / "probe.nsi"
    installer = root / "probe-installer.exe"
    uninstaller = root / "probe-uninstaller.exe"
    script.write_text(
        r'''Unicode true
RequestExecutionLevel user
!include "LogicLib.nsh"
!include "@HELPER@"
OutFile "@INSTALLER@"
Name "Synthetic purge decision test"
SilentInstall silent
!insertmacro ZhiyiDefinePurgeOptionFunction un.HasPurgeOption
Section
  WriteUninstaller "$EXEDIR\probe-uninstaller.exe"
SectionEnd
Function un.onInit
  StrCpy $4 "preserve"
  ${If} ${Silent}
    Call un.HasPurgeOption
    Pop $0
    ${If} $0 == "1"
      StrCpy $4 "purge"
      Delete "$INSTDIR\synthetic-data.txt"
    ${EndIf}
  ${Else}
    StrCpy $4 "interactive"
  ${EndIf}
  FileOpen $1 "$INSTDIR\decision.txt" w
  FileWrite $1 "$4"
  FileClose $1
FunctionEnd
Section Uninstall
SectionEnd
'''.replace("@HELPER@", _nsis_path(ROOT / "packaging/purge-option.nsh"))
           .replace("@INSTALLER@", _nsis_path(installer)),
        encoding="utf-8",
    )
    subprocess.run([MAKENSIS, "/INPUTCHARSET", "UTF8", str(script)], check=True, capture_output=True, timeout=30)
    subprocess.run([str(installer), "/S"], cwd=root, check=True, capture_output=True, timeout=15)
    assert uninstaller.is_file()
    return uninstaller


@pytest.mark.parametrize(
    "args,purge",
    [
        (["/S"], False),
        (["/S", "/PURGE"], True),
        (["/PURGE", "/S"], True),
        (["/S", "/purge"], True),
        (["/S", "/PURGE", "/OTHER"], True),
        (["/S", "/PURGE", "0"], True),
        (["/S", "/PURGEX"], False),
        (["/S", "/PURGE/OTHER"], False),
        (["/S", "/PURGE=0"], False),
        (["/S", "/PURGE=false"], False),
        (["/S", "/PURＧE"], False),
        (["/S", "／PURGE"], False),
        (["/S", "/OTHER=/PURGE"], False),
        (["/S", "--value=/PURGE"], False),
        (["/S", "quoted value /PURGE"], False),
        (["/S", '"/PURGE"'], False),
        (["/S", "資料 /PURGE"], False),
        (["/S", "/PURGEX", "/PURGE"], True),
        ('/S "/PURGE"', True),
        ('/S "/PURGEX"', False),
        ('/S "/PURGE=0"', False),
        ('/S "/OTHER=/PURGE"', False),
    ],
)
def test_only_a_complete_silent_purge_argument_removes_synthetic_data(probe_uninstaller, tmp_path, args, purge):
    case = tmp_path / "安装 测试"
    case.mkdir()
    uninstaller = case / "Uninstall.exe"
    shutil.copyfile(probe_uninstaller, uninstaller)
    marker = case / "synthetic-data.txt"
    marker.write_text("synthetic data only", encoding="utf-8")
    temp = case / "temp"
    temp.mkdir()
    env = os.environ.copy()
    env.update({"TEMP": str(temp), "TMP": str(temp)})
    command = f'"{uninstaller}" {args}' if isinstance(args, str) else [str(uninstaller), *args]
    result = subprocess.run(command, cwd=case, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    assert result.returncode == 0
    decision = case / "decision.txt"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if decision.exists() and decision.read_text(encoding="utf-8") in ("preserve", "purge", "interactive"):
            break
        time.sleep(0.05)
    assert decision.read_text(encoding="utf-8") == ("purge" if purge else "preserve")
    assert marker.exists() is not purge


@pytest.mark.parametrize("smoke", [False, True])
def test_product_installer_script_compiles_with_synthetic_payload(tmp_path, smoke):
    build = tmp_path / "dist"
    payload = build / "Zhiyi"
    payload.mkdir(parents=True)
    (payload / "Zhiyi.exe").write_bytes(b"synthetic GUI payload")
    (payload / "ZhiyiCLI.exe").write_bytes(b"synthetic CLI payload")
    output = tmp_path / "output"
    output.mkdir()
    args = [MAKENSIS, "/INPUTCHARSET", "UTF8", "/DAPP_VERSION=0.0.0",
            f"/DBUILD_ROOT={build}", f"/DOUTPUT_DIR={output}"]
    if smoke:
        args += ["/DSMOKE_MODE", f"/DSMOKE_DATA_DIR={tmp_path / 'synthetic-data'}"]
    args.append(str(ROOT / "packaging/installer.nsi"))
    result = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, errors="replace", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    suffix = "-smoke-setup.exe" if smoke else "-setup.exe"
    assert (output / ("Zhiyi-0.0.0-win-x64" + suffix)).is_file()
