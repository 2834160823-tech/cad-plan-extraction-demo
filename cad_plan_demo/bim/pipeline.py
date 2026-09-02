from __future__ import annotations

from pathlib import Path

from .ai_client import generate_revit_execution_plan, require_standard_model_from_llm
from .excel_reader import read_design_notes, read_fixed_excel
from .normalizer import normalize_workbook
from .outputs import write_bim_outputs
from .validator import apply_validation


def run_bim_pipeline(
    excel_path: str | Path,
    notes_path: str | Path,
    out_dir: str | Path,
    *,
    memory_context: dict | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> dict:
    excel_path = Path(excel_path)
    notes_path = Path(notes_path)
    workbook = read_fixed_excel(excel_path)
    notes = read_design_notes(notes_path)
    mechanical_input = normalize_workbook(workbook, notes, str(excel_path), str(notes_path))

    selected, component_ai_info = require_standard_model_from_llm(
        mechanical_input,
        notes,
        memory_context=memory_context,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )

    validated = apply_validation(selected)
    execution_plan, plan_ai_info = generate_revit_execution_plan(
        validated,
        memory_context=memory_context,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
    validated["llm_revit_execution_plan"] = execution_plan
    ai_info = {
        "used": True,
        "required": True,
        "stages": [component_ai_info, plan_ai_info],
    }
    write_bim_outputs(validated, out_dir, ai_info)
    return validated
