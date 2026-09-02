from __future__ import annotations

import csv
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .excel_export import write_single_result_workbook


def ensure_dxf(input_path: str | Path) -> Path:
    path = Path(input_path)
    suffix = path.suffix.lower()
    if suffix == ".dxf":
        return path
    if suffix != ".dwg":
        raise ValueError(f"Unsupported input format: {path.suffix}. Please provide .dxf or .dwg.")

    converter = find_oda_converter()
    if converter is None:
        raise RuntimeError(
            "DWG input needs conversion to DXF first, but ODA File Converter was not found. "
            "Install ODA File Converter or export the drawing as DXF, then run this demo again."
        )

    tmp = Path(tempfile.mkdtemp(prefix="dwg_to_dxf_"))
    in_dir = tmp / "in"
    out_dir = tmp / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    copied = in_dir / path.name
    shutil.copy2(path, copied)

    cmd = [
        str(converter),
        str(in_dir),
        str(out_dir),
        "ACAD2018",
        "DXF",
        "0",
        "1",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    candidates = list(out_dir.glob("*.dxf"))
    if not candidates:
        raise RuntimeError("ODA File Converter finished but no DXF file was produced.")
    return candidates[0]


def find_oda_converter() -> Path | None:
    names = ["ODAFileConverter.exe", "ODAFileConverter"]
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)

    common_roots = [
        Path("C:/Program Files/ODA/ODAFileConverter"),
        Path("C:/Program Files (x86)/ODA/ODAFileConverter"),
    ]
    for root in common_roots:
        if root.exists():
            matches = list(root.rglob("ODAFileConverter.exe"))
            if matches:
                return matches[0]
    return None


def write_outputs(result: dict, out_dir: str | Path) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / "objects.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    notes = result.get("notes", {})
    (out / "drawing_info.json").write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "general_notes.txt").write_text(str(notes.get("full_text", "")), encoding="utf-8")
    (out / "general_notes.json").write_text(
        json.dumps(notes.get("structured", {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    plan_summary = result.get("plan_summary", {})
    (out / "plan_summary.json").write_text(json.dumps(plan_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "coordinate_system.json").write_text(
        json.dumps(result.get("coordinate_system", {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(out / "floor_heights.csv", plan_summary.get("floor_heights", []))
    _write_csv(out / "elevation_marks.csv", plan_summary.get("elevation_marks", []))
    _write_csv(out / "walls.csv", result["walls"])
    _write_csv(out / "doors_windows.csv", result["openings"])
    _write_csv(out / "axes.csv", result["axes"])
    _write_csv(out / "issues.csv", result["issues"])
    relations = result.get("relations", {})
    _write_csv(out / "object_relations.csv", relations.get("object_relations", []))
    _write_csv(out / "context_summaries.csv", relations.get("context_summaries", []))

    summary = [
        "CAD architectural extraction demo summary",
        "",
        f"Drawing type: {notes.get('drawing_type', 'unknown')}",
        f"Drawing type name: {notes.get('drawing_type_name', '')}",
        f"Drawing title: {notes.get('drawing_title', '')}",
        f"Drawing title confidence: {notes.get('drawing_title_confidence', 0)}",
        f"Coordinate origin: {result.get('coordinate_system', {}).get('origin', '')}",
        f"Coordinate origin source: {result.get('coordinate_system', {}).get('source', '')}",
        f"Door count: {plan_summary.get('counts', {}).get('doors', 0)}",
        f"Window count: {plan_summary.get('counts', {}).get('windows', 0)}",
        f"Floor height candidates: {len(plan_summary.get('floor_heights', []))}",
        f"Elevation marks: {len(plan_summary.get('elevation_marks', []))}",
        f"CAD text items: {notes.get('text_count', 0)}",
        f"CAD text characters: {notes.get('text_char_count', 0)}",
        f"Drawing type scores: {notes.get('drawing_type_scores', {})}",
        "",
        f"Entities read: {result['counts']['entities']}",
        f"Segments extracted: {result['counts']['segments']}",
        f"Walls recognized: {result['counts']['walls']}",
        f"Doors/windows recognized: {result['counts']['openings']}",
        f"Axes recognized: {result['counts']['axes']}",
        f"Issues: {result['counts']['issues']}",
        f"Object relations: {len(relations.get('object_relations', []))}",
        f"Context summaries: {len(relations.get('context_summaries', []))}",
    ]
    (out / "summary.txt").write_text("\n".join(summary), encoding="utf-8")
    write_single_result_workbook(out / "drawing_report.xlsx", result)


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
