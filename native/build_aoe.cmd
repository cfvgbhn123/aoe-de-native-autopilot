@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0.."
set "AOE_BUILD_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "AOE_BUILD_PYTHON=%CD%\.venv\Scripts\python.exe"
if defined AOE_AI_PYTHON set "AOE_BUILD_PYTHON=%AOE_AI_PYTHON%"
"%AOE_BUILD_PYTHON%" -X utf8 native\build_aoe.py
exit /b %ERRORLEVEL%
