@echo off
chcp 65001 >nul
echo ==========================================
echo   AI 测试 - 移植包安装
echo ==========================================
echo.
set /p PROJ="项目路径（直接回车=仅装全局Skill，使用上级目录装Rule）: "
if "%PROJ%"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -ProjectRoot "%PROJ%"
)
echo.
pause
