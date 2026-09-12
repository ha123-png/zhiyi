Unicode true
RequestExecutionLevel user
SetCompressor /SOLID zlib

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!ifndef APP_VERSION
  !define APP_VERSION "0.3.0"
!endif
!ifndef BUILD_ROOT
  !error "BUILD_ROOT must point to the PyInstaller dist directory"
!endif
!ifndef OUTPUT_DIR
  !define OUTPUT_DIR "${BUILD_ROOT}\installer"
!endif
!ifdef SMOKE_MODE
  !ifndef SMOKE_DATA_DIR
    !error "SMOKE_DATA_DIR is required in smoke mode"
  !endif
!endif

!define APP_NAME "知意"
!define APP_EXE "Zhiyi.exe"
!define APP_CLI_EXE "ZhiyiCLI.exe"
!define APP_ICON "$INSTDIR\_internal\desktop\app.ico"
!define APP_KEY "Software\Zhiyi"
!define LEGACY_APP_KEY "Software\DocumentPipeline"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Zhiyi"
!define LEGACY_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\DocumentPipeline"
!define USER_DATA_DIR "$LOCALAPPDATA\Zhiyi"
!define LEGACY_USER_DATA_DIR "$LOCALAPPDATA\DocumentPipeline"

Var DeleteUserData

Name "${APP_NAME}"
!ifdef SMOKE_MODE
OutFile "${OUTPUT_DIR}\Zhiyi-${APP_VERSION}-win-x64-smoke-setup.exe"
!else
OutFile "${OUTPUT_DIR}\Zhiyi-${APP_VERSION}-win-x64-setup.exe"
!endif
InstallDir "$LOCALAPPDATA\Programs\Zhiyi"
InstallDirRegKey HKCU "${APP_KEY}" "InstallDir"
BrandingText "知意"
ShowInstDetails show
ShowUninstDetails show

!define MUI_ABORTWARNING
!define MUI_ICON "${BUILD_ROOT}\..\..\..\packaging\app.ico"
!define MUI_UNICON "${BUILD_ROOT}\..\..\..\packaging\uninstall.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "启动${APP_NAME}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
!ifndef SMOKE_MODE
  ReadRegStr $0 HKCU "${APP_KEY}" "InstallDir"
  ${If} $0 != ""
    StrCpy $INSTDIR $0
  ${EndIf}
!endif
FunctionEnd

Section "主程序" SEC_MAIN
  ; Only ask the exact executable from the previous installation to stop.
  ; The supervisor validates its own local control token and child processes.
  IfFileExists "$INSTDIR\${APP_CLI_EXE}" 0 +2
!ifdef SMOKE_MODE
    ExecWait '"$INSTDIR\${APP_CLI_EXE}" stop --data-dir "${SMOKE_DATA_DIR}"'
!else
    ExecWait '"$INSTDIR\${APP_CLI_EXE}" stop'
!endif
  ; The native window host can outlive its already-stopped supervisor. End
  ; Stage the new runtime so upgrades from older versions can use the scoped
  ; stop helper. NSIS deduplicates these files against the installed payload.
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\payload"
  File /r "${BUILD_ROOT}\Zhiyi\*"
  nsExec::ExecToLog '"$PLUGINSDIR\payload\${APP_CLI_EXE}" stop-installation --installation-dir "$INSTDIR"'
  Pop $0
  ${If} $0 != 0
    Abort "无法关闭当前安装目录中的知意，请退出该程序后重试。"
  ${EndIf}
  SetOutPath "$INSTDIR"
  File /r "${BUILD_ROOT}\Zhiyi\*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

!ifndef SMOKE_MODE
  WriteRegStr HKCU "${APP_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${APP_KEY}" "Version" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "知意"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  DeleteRegKey HKCU "${LEGACY_UNINSTALL_KEY}"
  DeleteRegKey HKCU "${LEGACY_APP_KEY}"

  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "${APP_ICON}" 0
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\诊断${APP_NAME}.lnk" "$INSTDIR\${APP_CLI_EXE}" "diagnose"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\卸载${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe" "" "$INSTDIR\Uninstall.exe" 0
!endif
SectionEnd

!ifndef SMOKE_MODE
Section "桌面快捷方式" SEC_DESKTOP
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "${APP_ICON}" 0
SectionEnd
!endif

Function un.onInit
  StrCpy $DeleteUserData "0"
!ifndef SMOKE_MODE
  ${GetParameters} $0
  ${GetOptions} $0 "/PURGE" $1
  ${IfNot} ${Silent}
    MessageBox MB_YESNOCANCEL|MB_ICONQUESTION "是否同时删除知意的全部本地数据？选择‘是’会删除文件历史、仪表盘统计、模板、设置和缓存；选择‘否’只卸载程序并保留数据；选择‘取消’将退出卸载。" IDYES purge_data IDNO keep_data
    Abort
    purge_data:
      StrCpy $DeleteUserData "1"
      Goto purge_choice_done
    keep_data:
      StrCpy $DeleteUserData "0"
      Goto purge_choice_done
    purge_choice_done:
  ${ElseIf} $1 != ""
    ; Silent uninstall preserves data unless the caller explicitly opts in
    ; with /PURGE, so automated software management cannot erase records.
    StrCpy $DeleteUserData "1"
  ${EndIf}
!endif
  IfFileExists "$INSTDIR\${APP_CLI_EXE}" 0 +2
!ifdef SMOKE_MODE
    ExecWait '"$INSTDIR\${APP_CLI_EXE}" stop --data-dir "${SMOKE_DATA_DIR}"'
!else
    nsExec::ExecToLog '"$INSTDIR\${APP_CLI_EXE}" stop'
!endif
  nsExec::ExecToLog '"$INSTDIR\${APP_CLI_EXE}" stop-installation --installation-dir "$INSTDIR"'
  Pop $0
  ${If} $0 != 0
    Abort "无法关闭当前安装目录中的知意，请退出该程序后重试。"
  ${EndIf}
FunctionEnd

Section "Uninstall"
!ifndef SMOKE_MODE
  Delete "$DESKTOP\${APP_NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APP_NAME}"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  DeleteRegKey HKCU "${APP_KEY}"
!endif
!ifndef SMOKE_MODE
  ${If} $DeleteUserData == "1"
    IfFileExists "$INSTDIR\${APP_CLI_EXE}" 0 +2
      nsExec::ExecToLog '"$INSTDIR\${APP_CLI_EXE}" purge-data --data-dir "${USER_DATA_DIR}"'
    RMDir /r "${USER_DATA_DIR}"
    IfFileExists "${LEGACY_USER_DATA_DIR}" 0 +2
      nsExec::ExecToLog '"$INSTDIR\${APP_CLI_EXE}" purge-data --data-dir "${LEGACY_USER_DATA_DIR}"'
    RMDir /r "${LEGACY_USER_DATA_DIR}"
  ${EndIf}
!endif
  RMDir /r "$INSTDIR"
SectionEnd
