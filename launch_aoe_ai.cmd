@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title AoE DE Native Autopilot
if not exist "aoe_ai_config.json" (
  copy /y "aoe_ai_config.example.json" "aoe_ai_config.json" >nul
  echo Created aoe_ai_config.json. Set game_dir to your game installation.
)
set "AOE_PYTHON="
if defined AOE_AI_PYTHON call :try_python "%AOE_AI_PYTHON%"
call :try_python "%~dp0.venv\Scripts\python.exe"
for /f "delims=" %%P in ('where.exe python.exe 2^>nul') do call :try_python "%%P"
if not defined AOE_PYTHON (
  echo Install 64-bit Python 3.10+ or set AOE_AI_PYTHON to its python.exe.
  pause
  exit /b 9009
)
"%AOE_PYTHON%" -X utf8 -u -m aoe_autopilot --replace %*
set "AOE_EXIT=%ERRORLEVEL%"
if not "%AOE_EXIT%"=="0" pause
exit /b %AOE_EXIT%
:try_python
if defined AOE_PYTHON exit /b
if not exist "%~1" exit /b
set "AOE_CANDIDATE=%~1"
if /i not "%AOE_CANDIDATE:\WindowsApps\=%"=="%AOE_CANDIDATE%" exit /b
"%~1" -I -c "import sys,ctypes;sys.exit(0 if sys.version_info >= (3,10) and ctypes.sizeof(ctypes.c_void_p)==8 and hasattr(ctypes,'WinDLL') else 1)" >nul 2>&1
if errorlevel 1 exit /b
set "AOE_PYTHON=%~1"
exit /b
