@echo off
setlocal

where py >nul 2>&1
if not errorlevel 1 (set "PY=py -3") else (set "PY=python")

if "%~1"=="" (
  set "INPUT=examples\sample_bim_input.xlsx"
  set "NOTES=examples\sample_design_notes.txt"
  set "OUT=outputs\bim_agent_sample"
  echo No input workbook was provided.
  echo Running the built-in BIM modeling agent sample.
) else (
  set "INPUT=%~1"
  set "NOTES=%~2"
  if "%NOTES%"=="" set "NOTES=examples\sample_design_notes.txt"
  set "OUT=outputs\%~n1_agent"
)

echo.
%PY% -m cad_plan_demo.bim_agent_main "%INPUT%" "%NOTES%" --out "%OUT%" --memory "agent_memory"

echo.
echo Output folder:
echo   %CD%\%OUT%
echo.
echo Agent output files:
echo   agent_trace.json
echo   agent_memory_context.json
echo   lessons_learned.json
echo   standard_model.json
echo   llm_revit_execution_plan.json
echo   revit_model_input.json
echo.
pause
