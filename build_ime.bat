@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   Mac-style IME Switcher v2.0 — 构建脚本
echo ============================================
echo.

:: 检查 PyInstaller 是否安装
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo [1/3] 安装依赖...
    pip install pystray pillow pyinstaller
    if %errorlevel% neq 0 (
        echo 依赖安装失败!
        pause
        exit /b 1
    )
) else (
    echo [1/3] 依赖已就绪
)

:: 确保运行时依赖也安装
pip show pystray >nul 2>&1
if %errorlevel% neq 0 (
    pip install pystray pillow
)

echo.
echo [2/3] 打包为 exe...

pyinstaller --onefile --noconsole --name "MacStyleIME" ^
    --hidden-import pystray ^
    --hidden-import PIL ^
    --hidden-import PIL.Image ^
    --hidden-import PIL.ImageDraw ^
    --hidden-import ime_switcher.config ^
    --hidden-import ime_switcher.winapi ^
    --hidden-import ime_switcher.toggle ^
    --hidden-import ime_switcher.hook ^
    --hidden-import ime_switcher.tray ^
    --distpath "." ^
    ime_switcher\__main__.py

if %errorlevel% neq 0 (
    echo 打包失败!
    pause
    exit /b 1
)

echo.
echo [3/3] 清理临时文件...
rmdir /s /q build 2>nul
del /q MacStyleIME.spec 2>nul

echo.
echo ============================================
echo   构建完成!
echo   输出文件: %~dp0MacStyleIME.exe
echo.
echo   用法:
echo     MacStyleIME.exe            直接运行
echo     MacStyleIME.exe --install  添加开机自启
echo     MacStyleIME.exe --uninstall 移除开机自启
echo ============================================
pause
