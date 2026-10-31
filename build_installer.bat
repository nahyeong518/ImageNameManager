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

 echo [1/4] Installing build tools...
py -3 -m pip install --upgrade pip Pillow PyInstaller
if errorlevel 1 goto BUILD_ERROR

 echo [2/4] Building ImageNameManager.exe...
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name ImageNameManager --collect-all PIL ImageNameManager.py
if errorlevel 1 goto BUILD_ERROR

 echo [3/4] Finding Inno Setup compiler...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC goto NO_INNO

 echo [4/4] Creating Setup.exe...
"%ISCC%" ImageNameManager_Setup.iss
if errorlevel 1 goto BUILD_ERROR

 echo.
 echo SUCCESS: Output\ImageNameManager_Setup.exe
if exist "Output\ImageNameManager_Setup.exe" start "" "%CD%\Output"
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
