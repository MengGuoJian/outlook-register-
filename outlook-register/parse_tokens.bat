@echo off
chcp 65001 >nul
echo ========================================
echo   Token 格式转换工具
echo ========================================
echo.

REM Python 路径
set PYTHON=D:\work\python.exe

REM 切换到项目目录
cd /d "%~dp0OutlookRegister-main"

REM 检查 client_id 参数
if "%~1"=="" (
    echo [错误] 请提供 client_id 参数
    echo.
    echo 用法: parse_tokens.bat YOUR_CLIENT_ID
    echo 示例: parse_tokens.bat M.C515_BL2.0.U.MsaArtifacts...
    echo.
    echo 或者先编辑 config.json 填入 client_id，然后直接运行:
    echo   parse_tokens.bat
    pause
    exit /b 1
)

echo [提示] 使用 client_id: %~1
echo.

REM 运行解析脚本
"%PYTHON%" parse_tokens.py %~1

echo.
echo ========================================
echo   转换完成！
echo ========================================
echo.
echo 结果文件: Results\formatted_tokens.txt
echo.
pause
