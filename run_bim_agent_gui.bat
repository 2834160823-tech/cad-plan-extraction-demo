@echo off
setlocal

cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (set "PY=py -3") else (set "PY=python")

echo Starting BIM Agent GUI...
%PY% -m cad_plan_demo.bim_agent_gui

if errorlevel 1 (
  echo.
  echo BIM Agent GUI stopped with an error.
  pause
)
