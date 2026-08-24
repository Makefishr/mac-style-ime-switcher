@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "WINDOWS_ROOT=%SystemRoot%"
set "SYSTEM32=%WINDOWS_ROOT%\System32"
set "POWERSHELL_EXE=%SYSTEM32%\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" (
    echo [ERROR] Windows PowerShell is required to validate the build environment.
    exit /b 1
)
set "TRUSTED_SYSTEM_PATH=%SYSTEM32%;%WINDOWS_ROOT%"
set "PATH=%TRUSTED_SYSTEM_PATH%"
set "PSModulePath=%SYSTEM32%\WindowsPowerShell\v1.0\Modules"

for /f "tokens=1 delims==" %%V in ('set CONDA 2^>nul') do set "%%V="
set "CONDA_DEFAULT_ENV="
set "CONDA_EXE="
set "CONDA_PREFIX="
set "CONDA_PREFIX_1="
set "CONDA_PROMPT_MODIFIER="
set "CONDA_PYTHON_EXE="
set "CONDA_SHLVL="
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONSTARTUP="
set "PYTHONUSERBASE="
set "VIRTUAL_ENV="
set "VIRTUAL_ENV_PROMPT="
set "__PYVENV_LAUNCHER__="
set "_CE_CONDA="
set "_CE_M="
set "PYTHONNOUSERSITE=1"
set "PIP_CONFIG_FILE=NUL"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_EXTRA_INDEX_URL="
set "PIP_INDEX_URL="
set "PIP_NO_INPUT=1"
set "PIP_PREFIX="
set "PIP_TARGET="
set "PIP_TRUSTED_HOST="
set "PIP_USER="

chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   Mac-style IME Switcher v1.4.0 - secure build
echo ============================================
echo.

set "ROOT=%~dp0"
set "VENV_DIR=%ROOT%.venv-build"
set "LOCK_FILE=%ROOT%requirements-build.lock"
set "BUILD_PATH=%ROOT%build"
set "SPEC_PATH=%ROOT%build"
set "DIST_PATH=%ROOT%dist"

"%POWERSHELL_EXE%" -NoLogo -NoProfile -NonInteractive -Command "$names=@('LOCALAPPDATA','MACSTYLEIME_BUILD_ROOT','MACSTYLEIME_DISTPATH','MACSTYLEIME_PYTHON'); foreach($name in $names){$value=[Environment]::GetEnvironmentVariable($name); if([string]::IsNullOrEmpty($value)){continue}; if($value.Contains([char]34) -or $value.Contains([char]13) -or $value.Contains([char]10)){exit 2}; if($value -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))'){exit 3}}; $python=[Environment]::GetEnvironmentVariable('MACSTYLEIME_PYTHON'); if(-not [string]::IsNullOrEmpty($python) -and (([IO.Path]::GetExtension($python) -ine '.exe') -or -not [IO.File]::Exists($python))){exit 4}; $dryRun=[Environment]::GetEnvironmentVariable('MACSTYLEIME_BUILD_DRY_RUN'); if(-not [string]::IsNullOrEmpty($dryRun) -and $dryRun -notin @('0','1')){exit 5}"
if errorlevel 1 (
    echo [ERROR] Build path and Python overrides must be safe absolute values.
    exit /b 1
)

if defined MACSTYLEIME_BUILD_ROOT (
    set "VENV_DIR=%MACSTYLEIME_BUILD_ROOT%\venv"
    set "BUILD_PATH=%MACSTYLEIME_BUILD_ROOT%\work"
    set "SPEC_PATH=%MACSTYLEIME_BUILD_ROOT%\spec"
    set "DIST_PATH=%MACSTYLEIME_BUILD_ROOT%\dist"
)
if defined MACSTYLEIME_DISTPATH set "DIST_PATH=%MACSTYLEIME_DISTPATH%"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "BUILD_PROCESS_PATH=%VENV_DIR%\Scripts;%TRUSTED_SYSTEM_PATH%"

set "BASE_PYTHON="
set "BASE_PYTHON_ARG="
if defined MACSTYLEIME_PYTHON goto :use_explicit_python
goto :find_default_python

:use_explicit_python
set "BASE_PYTHON=%MACSTYLEIME_PYTHON%"
goto :python_selected

:find_default_python
set "PY_LAUNCHER=%WINDOWS_ROOT%\py.exe"
if exist "%PY_LAUNCHER%" goto :verify_default_python
set "PY_LAUNCHER=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
if exist "%PY_LAUNCHER%" goto :verify_default_python
echo [ERROR] A trusted absolute Python launcher was not found.
exit /b 1

:verify_default_python
set "MACSTYLEIME_DEFAULT_LAUNCHER=%PY_LAUNCHER%"
"%POWERSHELL_EXE%" -NoLogo -NoProfile -NonInteractive -Command "$path=[Environment]::GetEnvironmentVariable('MACSTYLEIME_DEFAULT_LAUNCHER'); $signature=Get-AuthenticodeSignature -LiteralPath $path; if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation'){exit 1}"
if errorlevel 1 (
    echo [ERROR] The default Python launcher could not be verified.
    exit /b 1
)
set "MACSTYLEIME_DEFAULT_LAUNCHER="
set "BASE_PYTHON=%PY_LAUNCHER%"
set "BASE_PYTHON_ARG=-3.12"

:python_selected

if not exist "%LOCK_FILE%" (
    echo [ERROR] Missing requirements-build.lock; refusing to build.
    exit /b 1
)

echo [1/3] Checking CPython 3.12 x64 launcher...
"%BASE_PYTHON%" %BASE_PYTHON_ARG% -I -c "import sys; assert sys.version_info[0] == 3 and sys.version_info[1] == 12 and sys.maxsize > 2**32" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] The selected Python must be CPython 3.12 x64.
    exit /b 1
)
echo Python 3.12 x64 check passed.

set "PATH=%BUILD_PROCESS_PATH%"

if "%MACSTYLEIME_BUILD_DRY_RUN%"=="1" goto :dry_run

if not exist "%VENV_PYTHON%" (
    echo Creating project build environment: "%VENV_DIR%"
    "%BASE_PYTHON%" %BASE_PYTHON_ARG% -I -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create the isolated build environment.
        exit /b 1
    )
)
if not exist "%VENV_PYTHON%" (
    echo [ERROR] The isolated Python interpreter was not created.
    exit /b 1
)

echo Installing exact, hash-locked build dependencies...
"%VENV_PYTHON%" -m pip install --disable-pip-version-check --no-input --no-cache-dir --require-hashes -r "%LOCK_FILE%"
if errorlevel 1 (
    echo [ERROR] Hash-locked dependency installation failed; refusing to build.
    exit /b 1
)

echo.
echo [2/3] Building with the isolated interpreter...
if not exist "%ROOT%app.ico" (
    echo [ERROR] Missing app.ico; refusing to build.
    exit /b 1
)
"%VENV_PYTHON%" -m PyInstaller --noconfirm --onefile --noconsole --name "MacStyleIME" ^
    --hidden-import pystray ^
    --hidden-import PIL ^
    --hidden-import PIL.Image ^
    --hidden-import PIL.ImageDraw ^
    --hidden-import ime_switcher.config ^
    --hidden-import ime_switcher.winapi ^
    --hidden-import ime_switcher.toggle ^
    --hidden-import ime_switcher.hook ^
    --hidden-import ime_switcher.tray ^
    --hidden-import ime_switcher.settings ^
    --hidden-import ime_switcher.caps_ime ^
    --add-data "%ROOT%app.ico;." ^
    --icon "%ROOT%app.ico" ^
    --distpath "%DIST_PATH%" ^
    --workpath "%BUILD_PATH%" ^
    --specpath "%SPEC_PATH%" ^
    "%ROOT%ime_switcher\__main__.py"
if errorlevel 1 (
    echo [ERROR] PyInstaller failed; refusing to report a build.
    exit /b 1
)

echo.
echo [3/3] Build completed.
echo Output directory: "%DIST_PATH%"
endlocal
exit /b 0

:dry_run
echo [DRY RUN] No venv, pip, or PyInstaller process will be started.
echo [DRY RUN] Base Python: "%BASE_PYTHON%" %BASE_PYTHON_ARG%
echo [DRY RUN] Build PATH: "%PATH%"
echo [DRY RUN] "%VENV_PYTHON%" -m pip install --require-hashes -r "%LOCK_FILE%"
echo [DRY RUN] "%VENV_PYTHON%" -m PyInstaller --onefile --noconsole --name "MacStyleIME" --distpath "%DIST_PATH%" --workpath "%BUILD_PATH%" --specpath "%SPEC_PATH%"
endlocal
exit /b 0
