@echo off
setlocal

where py >nul 2>&1
if not errorlevel 1 (set "PY=py -3") else (set "PY=python")

if "%~1"=="" (
  set "INPUT=examples\sample_requirements_plan.dxf"
  set "OUT=outputs\sample_requirements_plan"
  echo No input file was provided.
  echo Running the built-in sample drawing:
  echo   %INPUT%
) else (
  set "INPUT=%~1"
  set "OUT=outputs\%~n1"
  echo Running CAD extraction for:
  echo   %INPUT%
)

echo.
%PY% -m cad_plan_demo.main "%INPUT%" --out "%OUT%"

echo.
echo Output folder:
echo   %CD%\%OUT%
echo.
echo Main result package:
echo   01_人工快速查看_中文识别报告.xlsx
echo   02_标准化模型数据\AI_Model.json
echo   02_标准化模型数据\AI_Elements.jsonl
echo   02_标准化模型数据\csv_tables\
echo   03_人工详细核查_完整报告.md
echo.
pause
