from __future__ import annotations

import csv
import json
from pathlib import Path

from .schema import MODEL_SEQUENCE, schema_copy


def write_bim_outputs(model: dict, out_dir: str | Path, ai_info: dict) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    _write_json(out / "standard_model.json", model)
    _write_json(out / "llm_revit_execution_plan.json", model.get("llm_revit_execution_plan", {}))
    _write_json(out / "revit_model_input.json", _revit_subset(model))
    _write_json(out / "bim_schema.json", schema_copy())
    _write_json(out / "ai_call_info.json", ai_info)
    _write_csv(out / "data_preview.csv", _preview_rows(model))
    _write_csv(out / "validation_issues.csv", model.get("validation", {}).get("issues", []))
    _write_csv(out / "component_statistics.csv", _statistics_rows(model))
    _write_csv(out / "component_details.csv", _detail_rows(model))
    _write_summary(out / "summary.txt", model, ai_info)


def _revit_subset(model: dict) -> dict:
    components = model.get("components", {})
    return {
        "schema_version": model.get("schema_version"),
        "project": model.get("project", {}),
        "model_sequence": list(MODEL_SEQUENCE),
        "components": {
            "levels": components.get("levels", []),
            "grids": components.get("grids", []),
            "columns": components.get("columns", []),
            "walls": components.get("walls", []),
            "slabs": components.get("slabs", []),
            "floor_openings": components.get("floor_openings", []),
            "stairs": components.get("stairs", []),
            "doors": components.get("doors", []),
            "windows": components.get("windows", []),
        },
        "llm_revit_execution_plan": model.get("llm_revit_execution_plan", {}),
        "validation": model.get("validation", {}),
    }


def _preview_rows(model: dict) -> list[dict]:
    rows: list[dict] = []
    for group, items in model.get("components", {}).items():
        for item in items:
            rows.append(
                {
                    "component_group": group,
                    "id": item.get("id", ""),
                    "type": item.get("type", ""),
                    "level": item.get("level") or item.get("base_level") or item.get("name", ""),
                    "source": item.get("source", ""),
                    "confidence": item.get("confidence", ""),
                    "review_status": item.get("review_status", ""),
                    "notes": item.get("notes", ""),
                }
            )
    return rows


def _statistics_rows(model: dict) -> list[dict]:
    rows: list[dict] = []
    for group, items in model.get("components", {}).items():
        rows.append(
            {
                "component_group": group,
                "input_count": len(items),
                "ready_count": sum(1 for item in items if item.get("review_status") in {"ready", "confirmed"}),
                "needs_review_count": sum(1 for item in items if item.get("review_status") == "needs_review"),
                "success_count": 0,
                "failed_count": 0,
                "status": "pending_revit",
            }
        )
    return rows


def _detail_rows(model: dict) -> list[dict]:
    rows: list[dict] = []
    for group, items in model.get("components", {}).items():
        for item in items:
            rows.append(
                {
                    "component_group": group,
                    "component_id": item.get("id", ""),
                    "input_status": item.get("review_status", ""),
                    "revit_element_id": "",
                    "success": "",
                    "failure_reason": "",
                    "default_values_used": _default_notes(item),
                    "manual_review_status": item.get("review_status", ""),
                }
            )
    return rows


def _default_notes(item: dict) -> str:
    source = str(item.get("source", ""))
    notes = str(item.get("notes", ""))
    flags = []
    if "default" in source.lower():
        flags.append(source)
    if "default" in notes.lower():
        flags.append(notes)
    return " | ".join(flags)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_summary(path: Path, model: dict, ai_info: dict) -> None:
    components = model.get("components", {})
    lines = [
        "AI Revit modeling data package",
        "",
        f"Project: {model.get('project', {}).get('name', '')}",
        f"Schema version: {model.get('schema_version', '')}",
        f"AI required: {ai_info.get('required', False)}",
        f"AI stages: {len(ai_info.get('stages', []))}",
        f"Requires human confirmation: {model.get('validation', {}).get('requires_human_confirmation', False)}",
        "",
        "Component counts:",
    ]
    for group, items in components.items():
        lines.append(f"- {group}: {len(items)}")
    lines.extend(
        [
            "",
            f"LLM Revit operations: {len(model.get('llm_revit_execution_plan', {}).get('operations', []))}",
            "",
            "Model sequence:",
            " -> ".join(model.get("validation", {}).get("model_sequence", list(MODEL_SEQUENCE))),
            "",
            f"Validation issues: {len(model.get('validation', {}).get('issues', []))}",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
