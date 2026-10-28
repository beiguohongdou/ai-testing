@echo off
chcp 65001 >nul
echo ==========================================
echo   AI Testing kit - install
echo ==========================================
echo.
set /p PROJ="项目路径（直接回车 = 只装全局技能）: "
if "%PROJ%"=="" goto skills_only
set /p AIGEN="同时把 aigen 框架装进项目？(y/N): "
if /i "%AIGEN%"=="y" goto with_aigen
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -ProjectRoot "%PROJ%"
goto end

:with_aigen
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -ProjectRoot "%PROJ%" -WithAigen
goto end

:skills_only
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"

:end
echo.
pause
