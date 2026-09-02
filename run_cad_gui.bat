@echo off
setlocal
where py >nul 2>&1
if not errorlevel 1 (set "PY=py -3") else (set "PY=python")
cd /d "%~dp0"
%PY% -m cad_plan_demo.gui
