@echo off
chcp 65001 >nul
echo ========================================
echo   OutlookRegister 依赖安装工具
echo ========================================
echo.

REM Python 路径（固定使用已配置好的 Python 3.11）
set PYTHON=D:\work\python.exe
set PIP=D:\work\Scripts\pip.exe
set PATCHRIGHT=D:\work\Scripts\patchright.exe

REM 切换到项目目录
cd /d "%~dp0OutlookRegister-main"

REM 检查 Python
if not exist "%PYTHON%" (
    echo [ERROR] Python not found: %PYTHON%
    pause
    exit /b 1
)

echo [1/3] Installing Python dependencies...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt

echo.
echo [2/3] Installing Chromium browser...
patchright install chromium

echo.
echo [3/3] Checking playwright dependencies...
playwright install chromium

echo.
echo ========================================
echo   Installation complete!
echo   Please edit config.json and run run.bat
echo ========================================
pause
