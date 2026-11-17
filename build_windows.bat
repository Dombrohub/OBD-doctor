@echo off
REM Builds OBD Doctor into Windows .exe files and an installer. Double-click this file.
REM Needs Python 3.9+ from https://www.python.org/downloads/ (tick "Add python.exe to PATH").
REM The installer step also needs NSIS from https://nsis.sourceforge.io (optional).
cd /d "%~dp0"
echo Installing build tools...
python -m pip install --upgrade pyinstaller pyserial anthropic || goto :error
echo Building programs...
python -m PyInstaller --noconfirm --clean obd_doctor.spec || goto :error

set "MAKENSIS="
where makensis >nul 2>nul && set "MAKENSIS=makensis"
if not defined MAKENSIS if exist "%ProgramFiles(x86)%\NSIS\makensis.exe" set "MAKENSIS=%ProgramFiles(x86)%\NSIS\makensis.exe"
if defined MAKENSIS (
    echo Building installer...
    "%MAKENSIS%" installer\obd_doctor.nsi || goto :error
) else (
    echo NSIS not found - skipping the installer. Install it from https://nsis.sourceforge.io to build one.
)

echo.
echo Done! Your files are in the "dist" folder:
echo   OBDDoctor-Setup.exe              - installer (if NSIS is installed)
echo   OBDDoctor\OBDDoctor.exe          - graphical app (portable: copy the whole OBDDoctor folder)
echo   OBDDoctor\obd-doctor-cli.exe     - command-line version
explorer dist
pause
exit /b 0

:error
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
