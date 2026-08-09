@echo off
chcp 65001 >nul
echo ========================================
echo   Outlook 登录测试工具
echo ========================================
echo.

set PYTHON=D:\work\python.exe

cd /d "%~dp0OutlookRegister-main"

echo 测试账号: vuoh1jon9yhkej@outlook.com
echo.
echo [提示] 浏览器将自动打开，请观察登录过程
echo [提示] 如果需要输入验证码，请在浏览器中手动操作
echo.

"%PYTHON%" test_login.py

echo.
echo ========================================
echo   测试完成
echo ========================================
pause
