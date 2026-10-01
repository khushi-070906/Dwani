@echo off
REM One-click local Windows build. Needs Python 3.12 installed (python.org, tick "Add to PATH")
REM and optionally Inno Setup 6 (for DwaniLive-Setup.exe). Run from a folder OUTSIDE OneDrive.
setlocal
cd /d "%~dp0"
echo %CD% | find /I "OneDrive" >nul && (echo ERROR: move this folder out of OneDrive first, e.g. C:\dev\Dwani & pause & exit /b 1)
py -3.12 --version >nul 2>&1 || (echo ERROR: Python 3.12 not found. Install from python.org & pause & exit /b 1)
if not exist .venv-build py -3.12 -m venv .venv-build
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-desktop.txt || (pause & exit /b 1)
python build_exe.py --backend pyinstaller || (echo BUILD FAILED - see above & pause & exit /b 1)
echo.
echo Done. Files are in: %CD%\release
explorer release
pause
