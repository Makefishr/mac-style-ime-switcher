@echo off
setlocal
cd /d "%~dp0"

set "BUILD_VENV=%~dp0.build-venv"
set "BUILD_PYTHON=%BUILD_VENV%\Scripts\python.exe"
set "BUILD_LOCK=%~dp0requirements-build.txt"
set "BUILD_OUTPUT=%~dp0MacStyleIME.exe"
set "PY_LAUNCHER=%SystemRoot%\py.exe"
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"

if not exist "%PY_LAUNCHER%" set "PY_LAUNCHER=%LocalAppData%\Programs\Python\Launcher\py.exe"

echo ============================================
echo   Mac-style IME Switcher build
echo ============================================
echo.

if exist "%BUILD_OUTPUT%" (
    del /f /q "%BUILD_OUTPUT%"
    if exist "%BUILD_OUTPUT%" (
        echo Unable to remove the previous build output: %BUILD_OUTPUT%
        goto :failed
    )
)

if not exist "%BUILD_LOCK%" (
    echo Build dependency lock not found: %BUILD_LOCK%
    goto :failed
)
if not exist "%PY_LAUNCHER%" (
    echo Windows Python Launcher not found. Install 64-bit Python 3.12.
    goto :failed
)

echo [1/3] Creating an isolated build environment and verifying dependencies...
if exist "%BUILD_VENV%" (
    rmdir /s /q "%BUILD_VENV%"
    if exist "%BUILD_VENV%" (
        echo Unable to remove the previous build environment: %BUILD_VENV%
        goto :failed
    )
)

"%PY_LAUNCHER%" -3.12 -m venv "%BUILD_VENV%"
if errorlevel 1 (
    echo Unable to create the build environment. Install 64-bit Python 3.12.
    goto :failed
)

"%BUILD_PYTHON%" -m pip --isolated --disable-pip-version-check install ^
    --no-input ^
    --index-url https://pypi.org/simple ^
    --only-binary=:all: ^
    --require-hashes ^
    -r "%BUILD_LOCK%"
if errorlevel 1 (
    echo Dependency installation or hash verification failed. No release was produced.
    goto :failed
)

echo.
echo [2/3] Building the executable...

"%BUILD_PYTHON%" -m PyInstaller --clean --noupx --onefile --noconsole --name "MacStyleIME" ^
    --hidden-import pystray ^
    --hidden-import PIL ^
    --hidden-import PIL.Image ^
    --hidden-import PIL.ImageDraw ^
    --hidden-import ime_switcher.config ^
    --hidden-import ime_switcher.winapi ^
    --hidden-import ime_switcher.toggle ^
    --hidden-import ime_switcher.hook ^
    --hidden-import ime_switcher.tray ^
    --hidden-import ime_switcher.caps_ime ^
    --hidden-import ime_switcher.elevation ^
    --hidden-import ime_switcher.settings_store ^
    --hidden-import ime_switcher.settings_ui ^
    --hidden-import tkinter ^
    --hidden-import tkinter.ttk ^
    --hidden-import tkinter.messagebox ^
    --add-data "app.ico;." ^
    --icon "app.ico" ^
    --distpath "." ^
    ime_switcher\__main__.py

if errorlevel 1 (
    echo Packaging failed. No release was produced.
    goto :failed
)

echo.
echo [3/3] Removing temporary build files...
rmdir /s /q build 2>nul
del /q MacStyleIME.spec 2>nul
rmdir /s /q "%BUILD_VENV%" 2>nul
if exist "%BUILD_VENV%" (
    echo Unable to remove the isolated build environment.
    goto :failed
)

echo.
echo ============================================
echo   Build completed.
echo   Output: %~dp0MacStyleIME.exe
echo.
echo   Usage:
echo     MacStyleIME.exe             Run normally
echo     MacStyleIME.exe --install   Enable auto-start
echo     MacStyleIME.exe --uninstall Disable auto-start
echo ============================================
if not defined MACSTYLEIME_BUILD_NO_PAUSE pause
exit /b 0

:failed
echo.
echo Build stopped. Fix the error above and try again.
rmdir /s /q build 2>nul
del /q MacStyleIME.spec 2>nul
rmdir /s /q "%BUILD_VENV%" 2>nul
del /f /q "%BUILD_OUTPUT%" 2>nul
if not defined MACSTYLEIME_BUILD_NO_PAUSE pause
exit /b 1
