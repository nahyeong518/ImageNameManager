@echo off
setlocal
cd /d "%~dp0"
title ImageNameManager Setup Builder

 echo ==================================================
 echo ImageNameManager - Windows Setup Builder
 echo ==================================================
 echo.

where py >nul 2>nul
if errorlevel 1 goto NO_PYTHON

 echo [1/4] Installing build tools and dependencies...
py -3 -m pip install --upgrade pip Pillow PyInstaller pystray
if errorlevel 1 goto BUILD_ERROR

 echo [2/4] Building ImageNameManager.exe with custom icon...
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name ImageNameManager --icon=app.ico --add-data "app.ico;." --add-data "app_icon.png;." --collect-all PIL --collect-all pystray ImageNameManager.py
if errorlevel 1 goto BUILD_ERROR

copy /y app.ico dist\ >nul
copy /y app_icon.png dist\ >nul

 echo [3/4] Finding Inno Setup compiler...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC goto NO_INNO

 echo [4/4] Creating Setup.exe...
if exist "Output\ImageNameManager_Setup.exe" del /f /q "Output\ImageNameManager_Setup.exe" >nul 2>nul
"%ISCC%" ImageNameManager_Setup.iss
if errorlevel 1 goto BUILD_ERROR

if exist "Output\ImageNameManager_v1.2.0_Setup.exe" (
    copy /y "Output\ImageNameManager_v1.2.0_Setup.exe" "%USERPROFILE%\Downloads\ImageNameManager_v1.2.0_Setup.exe" >nul
)

REM Refresh Windows Shell Icon Cache
ie4uinit.exe -show >nul 2>nul

 echo.
 echo ==================================================
 echo SUCCESS: Output\ImageNameManager_v1.2.0_Setup.exe
 echo ==================================================
if exist "Output\ImageNameManager_v1.2.0_Setup.exe" start "" "%CD%\Output"
pause
exit /b 0

:NO_PYTHON
echo ERROR: Python launcher was not found. Install Python and enable the py launcher.
pause
exit /b 1

:NO_INNO
echo ERROR: Inno Setup 6 compiler (ISCC.exe) was not found.
echo Reinstall Inno Setup 6 or check its installation folder.
pause
exit /b 1

:BUILD_ERROR
echo.
echo ERROR: Build failed. Read the error messages above.
pause
exit /b 1
