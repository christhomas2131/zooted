@echo off
setlocal

echo ============================================================
echo  Zooted Build Script
echo ============================================================
echo.

echo [1/4] Installing dependencies...
pip install pystray Pillow win10toast plyer pyinstaller
if errorlevel 1 (
    echo ERROR: pip install failed. Make sure Python and pip are in PATH.
    pause & exit /b 1
)
echo.

echo [2/4] Generating icon.ico...
python generate_icon.py
if errorlevel 1 (
    echo WARNING: Icon generation failed. Building without custom icon.
    set ICON_FLAG=
) else (
    set ICON_FLAG=--icon=icon.ico
)
echo.

echo [3/4] Cleaning previous build artifacts...
if exist build   rmdir /s /q build
if exist Zooted.spec del /q Zooted.spec
echo.

echo [4/4] Building Zooted.exe (this may take a minute)...
pyinstaller ^
    --onefile ^
    --noconsole ^
    --name Zooted ^
    --icon=cartoon_dock_icon.ico ^
    --add-data "logo_zoot.png;." ^
    --add-data "icon_v2.png;." ^
    --add-data "zooted_head_icon_plate_1024.png;." ^
    --add-data "zooted_head_icon_1024.png;." ^
    --hidden-import win10toast ^
    --hidden-import plyer ^
    --hidden-import plyer.platforms.win.notification ^
    zooted.py

if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller build failed. See output above.
    pause & exit /b 1
)

echo.
echo ============================================================
echo  Build complete!  ==>  dist\Zooted.exe
echo ============================================================
echo.
echo You can now move dist\Zooted.exe anywhere and run it directly.
echo No Python installation required on the target machine.
echo.
pause
