; OBD EFT - Windows installer (NSIS 3)
; Build:  makensis installer/obd_doctor.nsi      (from the project root)
; Output: dist/OBDEFT-Setup.exe
;
; Silent install:    OBDEFT-Setup.exe /S  [/D=C:\Path\To\Install]
; Silent uninstall:  "C:\Program Files\OBD EFT\uninstall.exe" /S
;
; The app used to be called "OBD Doctor". Installing removes that older copy
; (folder, shortcuts, Apps & features entry) so the user ends up with one app.

Unicode true
SetCompressor /SOLID lzma

!define APP_NAME      "OBD EFT"
!define APP_VERSION   "1.12.0"
!define APP_PUBLISHER "OBD EFT"
!define APP_EXE       "OBDEFT.exe"
!define CLI_EXE       "obd-eft-cli.exe"
!define UNINST_KEY    "Software\Microsoft\Windows\CurrentVersion\Uninstall\OBDEFT"
!define APP_KEY       "Software\OBDEFT"
!define OLD_NAME      "OBD Doctor"
!define OLD_UNINST    "Software\Microsoft\Windows\CurrentVersion\Uninstall\OBDDoctor"
!define OLD_KEY       "Software\OBDDoctor"
!define DIST          "..\dist"
!define APPDIR        "..\dist\OBDEFT"
!define ASSETS        "..\obd_doctor\assets"

Name "${APP_NAME}"
OutFile "${DIST}\OBDEFT-Setup.exe"  ; fixed name: each new version replaces the file
InstallDir "$PROGRAMFILES64\${APP_NAME}"
InstallDirRegKey HKLM "${APP_KEY}" "InstallDir"
RequestExecutionLevel admin
BrandingText "${APP_NAME} ${APP_VERSION}"
ShowInstDetails show
ShowUninstDetails show

; ---- installer file properties
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName"     "${APP_NAME}"
VIAddVersionKey "FileDescription" "${APP_NAME} Setup"
VIAddVersionKey "FileVersion"     "${APP_VERSION}"
VIAddVersionKey "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey "CompanyName"     "${APP_PUBLISHER}"
VIAddVersionKey "LegalCopyright"  "Provided as is, without warranty"

!include "MUI2.nsh"
!include "x64.nsh"
!include "FileFunc.nsh"

; ---- look
!define MUI_ICON   "${ASSETS}\icon.ico"
!define MUI_UNICON "${ASSETS}\icon.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP   "wizard.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "wizard.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP   "header.bmp"
!define MUI_HEADERIMAGE_UNBITMAP "header.bmp"
!define MUI_ABORTWARNING
!define MUI_COMPONENTSPAGE_SMALLDESC

; ---- installer pages
!define MUI_WELCOMEPAGE_TITLE "Welcome to ${APP_NAME} ${APP_VERSION} Setup"
!define MUI_WELCOMEPAGE_TEXT "This will install ${APP_NAME} on your computer.$\r$\n$\r$\n\
${APP_NAME} connects to your car through an ELM327 OBD-II adapter (USB, Bluetooth or Wi-Fi), \
reads engine, ABS and airbag trouble codes and live sensor data, and explains what is likely wrong - \
with optional plain-English AI explanations from Claude.$\r$\n$\r$\n\
Click Next to continue."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "license.txt"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${APP_NAME}"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp
!define MUI_FINISHPAGE_TEXT "${APP_NAME} has been installed.$\r$\n$\r$\n\
Tip: choose 'demo' as the adapter to try it without a car."
!insertmacro MUI_PAGE_FINISH

; ---- uninstaller pages
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ---- sections
; Close OBD EFT (and the old "OBD Doctor") if it's still running, so its files can be replaced
Function CloseRunningApp
  nsExec::ExecToStack 'cmd /c tasklist /FI "IMAGENAME eq ${APP_EXE}" /NH | find /I "${APP_EXE}"'
  Pop $0
  Pop $1
  ${If} $0 == 0
    ${IfNot} ${Silent}
      MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "${APP_NAME} is still running.$\r$\n$\r$\nClick OK to close it and continue installing, or Cancel to stop." IDOK close_it
      Abort
    ${EndIf}
    close_it:
  ${EndIf}
  nsExec::Exec 'taskkill /F /IM ${APP_EXE}'
  nsExec::Exec 'taskkill /F /IM ${CLI_EXE}'
  nsExec::Exec 'taskkill /F /IM OBDDoctor.exe'
  nsExec::Exec 'taskkill /F /IM obd-doctor-cli.exe'
  Sleep 1000
FunctionEnd

Section "${APP_NAME} (required)" SecMain
  SectionIn RO
  Call CloseRunningApp
  SetOutPath "$INSTDIR"
  ; App folder: both programs plus the shared _internal runtime folder
  RMDir /r "$INSTDIR\_internal"  ; clear the old runtime on upgrade
  File /r "${APPDIR}\*.*"
  File "/oname=icon.ico" "${ASSETS}\icon.ico"
  File "/oname=License.txt" "license.txt"
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; Remove the previous install from when the app was called "OBD Doctor"
  ReadRegStr $1 HKLM "${OLD_KEY}" "InstallDir"
  ${If} $1 != ""
  ${AndIf} $1 != $INSTDIR
    Delete "$1\OBDDoctor.exe"
    Delete "$1\obd-doctor-cli.exe"
    RMDir /r "$1\_internal"
    Delete "$1\icon.ico"
    Delete "$1\License.txt"
    Delete "$1\uninstall.exe"
    RMDir "$1"  ; only removed if empty
  ${EndIf}
  Delete "$INSTDIR\OBDDoctor.exe"       ; old program files, if installed into the same folder
  Delete "$INSTDIR\obd-doctor-cli.exe"
  Delete "$SMPROGRAMS\${OLD_NAME}\${OLD_NAME}.lnk"
  Delete "$SMPROGRAMS\${OLD_NAME}\${OLD_NAME} Command Prompt.lnk"
  Delete "$SMPROGRAMS\${OLD_NAME}\Uninstall ${OLD_NAME}.lnk"
  RMDir "$SMPROGRAMS\${OLD_NAME}"
  Delete "$DESKTOP\${OLD_NAME}.lnk"
  DeleteRegKey HKLM "${OLD_UNINST}"
  DeleteRegKey HKLM "${OLD_KEY}"

  ; Start Menu
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0 \
    SW_SHOWNORMAL "" "Read your car's computer and diagnose problems"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME} Command Prompt.lnk" "$SYSDIR\cmd.exe" \
    '/k "cd /d "$INSTDIR" && echo OBD EFT command line - type: ${CLI_EXE} --help"' "$INSTDIR\icon.ico" 0
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk" "$INSTDIR\uninstall.exe"

  ; Apps & features entry
  WriteRegStr HKLM "${APP_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKLM "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKLM "${UNINST_KEY}" "EstimatedSize" "$0"
SectionEnd

Section "Desktop shortcut" SecDesktop
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain}    "The ${APP_NAME} app, command-line tool and Start Menu shortcuts."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "Put a ${APP_NAME} shortcut on the desktop."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "${APP_NAME} requires 64-bit Windows."
    Abort
  ${EndIf}
  SetRegView 64
  SetShellVarContext all  ; shortcuts for all users
FunctionEnd

Function LaunchApp
  ; Start through Explorer so the app does NOT inherit the installer's admin rights
  Exec '"$WINDIR\explorer.exe" "$INSTDIR\${APP_EXE}"'
FunctionEnd

; ---- uninstaller
Function un.onInit
  SetRegView 64
  SetShellVarContext all
FunctionEnd

Section "Uninstall"
  nsExec::Exec 'taskkill /F /IM ${APP_EXE}'
  nsExec::Exec 'taskkill /F /IM ${CLI_EXE}'
  Sleep 1000
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\${CLI_EXE}"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\icon.ico"
  Delete "$INSTDIR\License.txt"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"  ; only removed if empty (keeps any reports the user saved there)

  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME} Command Prompt.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"
  Delete "$DESKTOP\${APP_NAME}.lnk"

  DeleteRegKey HKLM "${UNINST_KEY}"
  DeleteRegKey HKLM "${APP_KEY}"
SectionEnd
