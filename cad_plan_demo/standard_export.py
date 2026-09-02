from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime
from math import atan2, degrees, hypot, isfinite
from pathlib import Path
from typing import Any

from .excel_export import write_human_review_workbook


SCHEMA_VERSION = "1.0"
EXPORTER_VERSION = "standard-export-1.4"
UNIT = "mm"
REVIEW_THRESHOLD = 0.80
DEFAULT_WALL_HEIGHT = 3000.0
DEFAULT_DOOR_HEIGHT = 2100.0
HUMAN_EXCEL_NAME = "01_人工快速查看_中文识别报告.xlsx"
MODEL_DIR_NAME = "02_标准化模型数据"
CSV_TABLE_DIR_NAME = "csv_tables"
AI_MODEL_NAME = "AI_Model.json"
AI_ELEMENTS_NAME = "AI_Elements.jsonl"
AI_README_NAME = "AI_Readme.md"
DETAIL_REPORT_NAME = "03_人工详细核查_完整报告.md"


CSV_SCHEMAS: dict[str, list[str]] = {
    "Manifest.csv": ["File_Name", "Data_Type", "Record_Count", "Schema_Version", "Generated_Time", "Status", "Remarks"],
    "Project_Info.csv": ["Project_ID", "Project_Name", "Source_File_Name", "Source_File_Type", "Unit", "Coordinate_System", "Origin_X", "Origin_Y", "Origin_Z", "Recognition_Time", "Exporter_Version", "Schema_Version"],
    "Drawings.csv": ["Drawing_ID", "Project_ID", "Drawing_Name", "Drawing_Number", "Drawing_Type", "Level_ID", "Scale", "Unit", "Source_File_Name", "Original_Min_X", "Original_Min_Y", "Original_Max_X", "Original_Max_Y", "Rotation_Angle", "Recognition_Confidence", "Needs_Review", "Remarks"],
    "Levels.csv": ["Level_ID", "Project_ID", "Level_Name", "Level_Number", "Elevation", "Floor_Height", "Source_Drawing_ID", "Confidence", "Needs_Review", "Remarks"],
    "Grids.csv": ["Grid_ID", "Project_ID", "Drawing_ID", "Level_ID", "Grid_Name", "Grid_Type", "Start_X", "Start_Y", "Start_Z", "End_X", "End_Y", "End_Z", "Angle", "Confidence", "Needs_Review", "Remarks"],
    "Walls.csv": ["Element_ID", "Element_Name", "Project_ID", "Drawing_ID", "Level_ID", "Wall_Type", "Start_X", "Start_Y", "Start_Z", "End_X", "End_Y", "End_Z", "Length", "Thickness", "Height", "Base_Offset", "Top_Offset", "Rotation_Angle", "Grid_Start", "Grid_End", "Grid_Offset_X", "Grid_Offset_Y", "Material", "Is_Exterior", "Confidence", "Source_Geometry_Count", "Needs_Review", "Remarks"],
    "Wall_Runs.csv": ["Wall_Run_ID", "Element_Name", "Project_ID", "Drawing_ID", "Level_ID", "Start_X", "Start_Y", "Start_Z", "End_X", "End_Y", "End_Z", "Length", "Thickness", "Height", "Source_Wall_IDs", "Source_Wall_Count", "Opening_IDs", "Opening_Count", "Rotation_Angle", "Confidence", "Needs_Review", "Remarks"],
    "Doors.csv": ["Element_ID", "Element_Name", "Project_ID", "Drawing_ID", "Level_ID", "Host_Wall_ID", "Host_Wall_Run_ID", "Door_Category", "Final_Category", "Mechanical_Category", "Mechanical_Category_Source", "Mechanical_Category_Confidence", "Needs_AI_Classification", "Classification_Input", "Door_Type", "Door_Mark", "Width", "Width_Source", "Height", "Height_Source", "Thickness", "Center_X", "Center_Y", "Center_Z", "Distance_From_Host_Start", "Opening_Direction", "Swing_Side", "Swing_Angle", "Swing_Source", "Swing_Confidence", "Panel_Start_X", "Panel_Start_Y", "Panel_End_X", "Panel_End_Y", "Panel_Thickness", "Panel_Wall_Angle", "Matched_Elevation_Drawing", "Matched_Elevation_Door_ID", "Cross_View_Match_Score", "Cross_View_Match_Status", "Grid_Reference", "Grid_Offset_X", "Grid_Offset_Y", "Confidence", "Source_Geometry_Count", "Needs_Review", "Remarks"],
    "Windows.csv": ["Element_ID", "Element_Name", "Project_ID", "Drawing_ID", "Level_ID", "Host_Wall_ID", "Host_Wall_Run_ID", "Window_Category", "Final_Category", "Mechanical_Category", "Mechanical_Category_Source", "Mechanical_Category_Confidence", "Needs_AI_Classification", "Classification_Input", "Window_Type", "Width", "Width_Source", "Height", "Height_Source", "Sill_Height", "Sill_Height_Source", "Matched_Elevation_Drawing", "Matched_Elevation_Window_ID", "Cross_View_Match_Score", "Center_X", "Center_Y", "Center_Z", "Distance_From_Host_Start", "Grid_Reference", "Grid_Offset_X", "Grid_Offset_Y", "Confidence", "Source_Geometry_Count", "Needs_Review", "Remarks"],
    "Columns.csv": ["Element_ID", "Element_Name", "Project_ID", "Drawing_ID", "Level_ID", "Column_Type", "Center_X", "Center_Y", "Base_Z", "Top_Z", "Width", "Depth", "Diameter", "Height", "Rotation_Angle", "Grid_Reference", "Grid_Offset_X", "Grid_Offset_Y", "Material", "Confidence", "Source_Geometry_Count", "Needs_Review", "Remarks"],
    "Beams.csv": ["Element_ID", "Element_Name", "Project_ID", "Drawing_ID", "Level_ID", "Beam_Type", "Start_X", "Start_Y", "Start_Z", "End_X", "End_Y", "End_Z", "Length", "Width", "Height", "Rotation_Angle", "Start_Support_ID", "End_Support_ID", "Material", "Confidence", "Source_Geometry_Count", "Needs_Review", "Remarks"],
    "Floors.csv": ["Element_ID", "Element_Name", "Project_ID", "Drawing_ID", "Level_ID", "Floor_Type", "Boundary_ID", "Boundary_Points", "Area", "Thickness", "Elevation", "Material", "Is_Closed_Boundary", "Opening_IDs", "Opening_Count", "Source", "Confidence", "Source_Geometry_Count", "Needs_Review", "Remarks"],
    "Roofs.csv": ["Element_ID", "Element_Name", "Project_ID", "Drawing_ID", "Level_ID", "Roof_Type", "Boundary_ID", "Boundary_Points", "Area", "Thickness", "Elevation", "Slope", "Drainage_Type", "Material", "Source", "Confidence", "Needs_Review", "Remarks"],
    "Parapets.csv": ["Element_ID", "Element_Name", "Project_ID", "Drawing_ID", "Level_ID", "Host_Roof_ID", "Parapet_Type", "Start_X", "Start_Y", "Start_Z", "End_X", "End_Y", "End_Z", "Length", "Thickness", "Height", "Height_Source", "Material", "Source", "Confidence", "Needs_Review", "Remarks"],
    "Floor_Openings.csv": ["Opening_ID", "Element_Name", "Project_ID", "Drawing_ID", "Level_ID", "Host_Floor_ID", "Opening_Type", "Boundary_Points", "Center_X", "Center_Y", "Center_Z", "Width", "Depth", "Area", "Source", "Confidence", "Source_Geometry_Count", "Needs_Review", "Remarks"],
    "Stairs.csv": ["Element_ID", "Element_Name", "Project_ID", "Drawing_ID", "Level_ID", "Stair_Type", "Stair_Core_ID", "Stair_Segment_ID", "Stair_Segment_Number", "Level_Span_Count", "Start_Level_ID", "End_Level_ID", "Start_X", "Start_Y", "Start_Z", "End_X", "End_Y", "End_Z", "Boundary_ID", "Boundary_Points", "Stairwell_Opening_ID", "Stairwell_Opening_Boundary", "Opening_Required", "Total_Rise", "Total_Run", "Width", "Stairwell_Width", "Run_Count", "Risers_Per_Run", "Treads_Per_Run", "Run_Length", "Landing_Length", "Landing_Width", "Riser_Height", "Tread_Depth", "Number_Of_Risers", "Number_Of_Treads", "Direction", "Confidence", "Source_Geometry_Count", "Needs_Review", "Remarks"],
    "Railings.csv": ["Element_ID", "Element_Name", "Project_ID", "Drawing_ID", "Level_ID", "Railing_Type", "Start_X", "Start_Y", "Start_Z", "End_X", "End_Y", "End_Z", "Height", "Distance_To_Stairwell", "Related_Stair_ID", "Source", "Confidence", "Source_Geometry_Count", "Needs_Review", "Remarks"],
    "Rooms.csv": ["Element_ID", "Element_Name", "Project_ID", "Drawing_ID", "Level_ID", "Room_Name", "Room_Number", "Area", "Perimeter", "Center_X", "Center_Y", "Center_Z", "Boundary_ID", "Confidence", "Needs_Review", "Remarks"],
    "Dimensions.csv": ["Dimension_ID", "Project_ID", "Drawing_ID", "Level_ID", "Dimension_Type", "Value", "Unit", "Start_X", "Start_Y", "End_X", "End_Y", "Associated_Element_ID", "Confidence", "Needs_Review", "Remarks"],
    "Text_Annotations.csv": ["Text_ID", "Project_ID", "Drawing_ID", "Level_ID", "Text_Content", "Original_X", "Original_Y", "Original_Z", "Local_X", "Local_Y", "Local_Z", "Layer", "Height", "Rotation_Angle", "Associated_Element_ID", "Associated_Element_Type", "Association_Distance", "Association_Method", "Confidence", "Needs_Review", "Remarks"],
    "Raw_Geometry.csv": ["Raw_Geometry_ID", "Project_ID", "Drawing_ID", "Layer", "Geometry_Type", "Start_X", "Start_Y", "End_X", "End_Y", "Center_X", "Center_Y", "Radius", "Text_Content", "Block_Name", "Point_Count", "Classified_As", "Associated_Element_ID", "Confidence", "Needs_Review", "Remarks"],
    "Element_Geometry_Map.csv": ["Element_ID", "Raw_Geometry_ID", "Project_ID", "Drawing_ID", "Relationship_Type", "Confidence", "Remarks"],
    "Opening_Wall_Run_Map.csv": ["Opening_ID", "Opening_Type", "Project_ID", "Drawing_ID", "Wall_Run_ID", "Host_Wall_ID", "Relationship_Type", "Distance_From_Run_Start", "Confidence", "Remarks"],
    "Materials.csv": ["Material_ID", "Project_ID", "Material_Name", "Material_Category", "Material_Class", "Manufacturer", "Model", "Finish", "Color", "Fire_Rating", "Thermal_Conductivity", "Density", "Source", "Confidence", "Needs_Review", "Remarks"],
    "Element_Material_Map.csv": ["Element_ID", "Element_Type", "Project_ID", "Drawing_ID", "Material_ID", "Material_Role", "Material_Name", "Source", "Confidence", "Needs_Review", "Remarks"],
    "Uncertain_Elements.csv": ["Element_ID", "Element_Type", "Project_ID", "Drawing_ID", "Reason", "Confidence", "Recommended_Action"],
}

EMPTY_TABLES = ["Beams.csv", "Rooms.csv"]


@dataclass
class StandardExportResult:
    output_dir: Path
    model_dir: Path
    csv_dir: Path
    ai_model: Path
    ai_elements: Path
    validation_errors: list[dict[str, Any]]
    human_report: Path
    detailed_report: Path


def write_standard_project_outputs(
    output_root: str | Path,
    drawing_results: list[tuple[str, dict]],
    source_path: str | Path,
    project_name: str | None = None,
    *,
    language: str = "zh",
    translation_api_key: str | None = None,
    translation_base_url: str | None = None,
    translation_model: str | None = None,
) -> StandardExportResult:
    # These options belong to the console/export contract. The current
    # deterministic workbook remains Chinese; accepting the options keeps
    # English UI runs compatible without changing the established output.
    _ = (language, translation_api_key, translation_base_url, translation_model)
    now = datetime.now()
    root = Path(output_root)
    source = Path(source_path)
    project_name = safe_name(project_name or source.stem or "Project")
    project_id = "PROJECT-001"
    export_dir = unique_dir(root / f"{project_name}_{now.strftime('%Y%m%d_%H%M%S')}")
    human_report = export_dir / HUMAN_EXCEL_NAME
    model_dir = export_dir / MODEL_DIR_NAME
    csv_dir = model_dir / CSV_TABLE_DIR_NAME
    ai_model = model_dir / AI_MODEL_NAME
    ai_elements = model_dir / AI_ELEMENTS_NAME
    ai_readme = model_dir / AI_README_NAME
    detailed_report = export_dir / DETAIL_REPORT_NAME
    model_dir.mkdir(parents=True, exist_ok=True)
    csv_dir.mkdir(parents=True, exist_ok=True)

    tables = build_standard_tables(project_id, project_name, drawing_results, source, now)
    validation_errors = validate_tables(tables)
    tables["Uncertain_Elements.csv"].extend(uncertain_rows(tables, validation_errors))

    # Stairwell openings can be projected from a stair detail onto other floor
    # drawings.  Include those project-level results in the human workbook too.
    write_human_review_workbook(human_report, human_report_drawing_results(drawing_results, tables))
    write_detailed_report(detailed_report, project_id, project_name, drawing_results, tables, validation_errors, now)

    for file_name, headers in CSV_SCHEMAS.items():
        if file_name == "Manifest.csv":
            continue
        rows = tables.get(file_name, [])
        write_fixed_csv(csv_dir / file_name, headers, rows)

    manifest = build_manifest(tables, now)
    write_fixed_csv(csv_dir / "Manifest.csv", CSV_SCHEMAS["Manifest.csv"], manifest)
    write_ai_model_files(ai_model, ai_elements, ai_readme, project_id, project_name, tables, validation_errors, now)

    return StandardExportResult(export_dir, model_dir, csv_dir, ai_model, ai_elements, validation_errors, human_report, detailed_report)


def human_report_drawing_results(
    drawing_results: list[tuple[str, dict]], tables: dict[str, list[dict[str, Any]]]
) -> list[tuple[str, dict]]:
    """Add project-projected stairwell openings to their host-floor report pages."""
    report_results: list[tuple[str, dict]] = []
    drawing_indexes: dict[str, int] = {}
    for index, (name, result) in enumerate(drawing_results, start=1):
        copied = dict(result)
        copied["floor_openings"] = list(result.get("floor_openings", []))
        copied["parapets"] = []
        report_results.append((name, copied))
        drawing_indexes[f"DRAWING-{index:03d}"] = index - 1

    for opening in tables.get("Floor_Openings.csv", []):
        if opening.get("Opening_Type") != "stairwell_opening":
            continue
        report_index = drawing_indexes.get(str(opening.get("Drawing_ID") or ""))
        if report_index is None:
            continue
        _, result = report_results[report_index]
        opening_id = str(opening.get("Opening_ID") or "")
        if any(str(item.get("id") or "") == opening_id for item in result["floor_openings"]):
            continue
        result["floor_openings"].append(
            {
                "id": opening_id,
                "opening_type": "stairwell_opening",
                "local_boundary_points": parse_point_pairs(opening.get("Boundary_Points")),
                "local_center": (number(opening.get("Center_X")), number(opening.get("Center_Y"))),
                "width": number(opening.get("Width")),
                "depth": number(opening.get("Depth")),
                "host_floor_id": opening.get("Host_Floor_ID", ""),
                "source": opening.get("Source", ""),
                "confidence": number(opening.get("Confidence")),
                "needs_review": str(opening.get("Needs_Review") or "").lower() == "true",
            }
        )
    for parapet in tables.get("Parapets.csv", []):
        report_index = drawing_indexes.get(str(parapet.get("Drawing_ID") or ""))
        if report_index is None:
            continue
        _, result = report_results[report_index]
        result["parapets"].append(
            {
                "id": parapet.get("Element_ID", ""),
                "parapet_type": parapet.get("Parapet_Type", ""),
                "local_start": (number(parapet.get("Start_X")), number(parapet.get("Start_Y"))),
                "local_end": (number(parapet.get("End_X")), number(parapet.get("End_Y"))),
                "length": number(parapet.get("Length")),
                "thickness_mm": number(parapet.get("Thickness")),
                "height_mm": number(parapet.get("Height")),
                "height_source": parapet.get("Height_Source", ""),
                "host_roof_id": parapet.get("Host_Roof_ID", ""),
                "source": parapet.get("Source", ""),
                "confidence": number(parapet.get("Confidence")),
                "needs_review": str(parapet.get("Needs_Review") or "").lower() == "true",
                "remarks": parapet.get("Remarks", ""),
            }
        )
    return report_results


def build_standard_tables(
    project_id: str,
    project_name: str,
    drawing_results: list[tuple[str, dict]],
    source_path: Path,
    now: datetime,
) -> dict[str, list[dict[str, Any]]]:
    tables = {name: [] for name in CSV_SCHEMAS if name != "Manifest.csv"}
    tables["Project_Info.csv"].append(
        {
            "Project_ID": project_id,
            "Project_Name": project_name,
            "Source_File_Name": source_path.name,
            "Source_File_Type": source_path.suffix.lower().lstrip("."),
            "Unit": UNIT,
            "Coordinate_System": "local",
            "Origin_X": 0,
            "Origin_Y": 0,
            "Origin_Z": 0,
            "Recognition_Time": now.isoformat(timespec="seconds"),
            "Exporter_Version": EXPORTER_VERSION,
            "Schema_Version": SCHEMA_VERSION,
        }
    )

    id_maps: dict[tuple[str, str, str], str] = {}
    counters = {"DRAWING": 0, "LEVEL": 0, "GRID": 0, "WALL": 0, "WALLRUN": 0, "COLUMN": 0, "FLOOR": 0, "FLOOROPENING": 0, "STAIR": 0, "RAILING": 0, "PARAPET": 0, "DOOR": 0, "WINDOW": 0, "TEXT": 0, "DIM": 0, "RAW": 0}
    pending_stair_openings: list[dict[str, Any]] = []
    level_id = "LEVEL-001"
    counters["LEVEL"] = 1
    default_floor_height = first_floor_height(drawing_results) or DEFAULT_WALL_HEIGHT
    tables["Levels.csv"].append(
        {
            "Level_ID": level_id,
            "Project_ID": project_id,
            "Level_Name": "默认楼层",
            "Level_Number": 1,
            "Elevation": 0,
            "Floor_Height": round(default_floor_height, 3),
            "Source_Drawing_ID": "",
            "Confidence": 0.5,
            "Needs_Review": "true",
            "Remarks": "当前图纸未提供完整楼层表，导出器创建默认楼层。",
        }
    )

    drawing_level_ids, inferred_level_rows = infer_project_levels(project_id, drawing_results, default_floor_height)
    tables["Levels.csv"] = inferred_level_rows
    counters["LEVEL"] = len(inferred_level_rows)

    for drawing_index, (drawing_name, result) in enumerate(drawing_results, start=1):
        counters["DRAWING"] += 1
        drawing_id = f"DRAWING-{counters['DRAWING']:03d}"
        level_id = drawing_level_ids.get(drawing_name, "")
        notes = result.get("notes", {})
        frame = result.get("frame", {})
        drawing_type = normalize_drawing_type(notes.get("drawing_type"))
        display_name = display_drawing_name(drawing_name, result)
        level_unassigned = not bool(level_id)
        tables["Drawings.csv"].append(
            {
                "Drawing_ID": drawing_id,
                "Project_ID": project_id,
                "Drawing_Name": display_name,
                "Drawing_Number": drawing_index,
                "Drawing_Type": drawing_type,
                "Level_ID": level_id,
                "Scale": "",
                "Unit": UNIT,
                "Source_File_Name": Path(str(result.get("input", {}).get("original", source_path.name))).name,
                "Original_Min_X": frame.get("min_x", ""),
                "Original_Min_Y": frame.get("min_y", ""),
                "Original_Max_X": frame.get("max_x", ""),
                "Original_Max_Y": frame.get("max_y", ""),
                "Rotation_Angle": 0,
                "Recognition_Confidence": notes.get("drawing_title_confidence", ""),
                "Needs_Review": bool_text(level_unassigned or (notes.get("drawing_title_confidence") or 0) < REVIEW_THRESHOLD),
                "Remarks": "图纸未绑定具体楼层。" if level_unassigned else "",
            }
        )

        for axis in sorted(result.get("axes", []), key=lambda a: sort_point(a.get("local_start") or a.get("start"))):
            counters["GRID"] += 1
            grid_id = f"GRID-{counters['GRID']:03d}"
            id_maps[("grid", drawing_name, str(axis.get("id")))] = grid_id
            start = point(axis.get("local_start") or axis.get("start"))
            end = point(axis.get("local_end") or axis.get("end"))
            tables["Grids.csv"].append(
                {
                    "Grid_ID": grid_id,
                    "Project_ID": project_id,
                    "Drawing_ID": drawing_id,
                    "Level_ID": level_id,
                    "Grid_Name": axis.get("name") or axis.get("id") or grid_id,
                    "Grid_Type": "linear",
                    "Start_X": value_at(start, 0),
                    "Start_Y": value_at(start, 1),
                    "Start_Z": 0,
                    "End_X": value_at(end, 0),
                    "End_Y": value_at(end, 1),
                    "End_Z": 0,
                    "Angle": angle(start, end),
                    "Confidence": axis.get("confidence", ""),
                    "Needs_Review": bool_text((axis.get("confidence") or 0) < REVIEW_THRESHOLD),
                    "Remarks": "",
                }
            )

        for wall in sorted(result.get("walls", []), key=lambda w: sort_point(w.get("local_start") or w.get("start"))):
            counters["WALL"] += 1
            wall_id = f"WALL-{counters['WALL']:03d}"
            id_maps[("wall", drawing_name, str(wall.get("id")))] = wall_id
            start = point(wall.get("local_start") or wall.get("start"))
            end = point(wall.get("local_end") or wall.get("end"))
            height = number(wall.get("height_mm")) or DEFAULT_WALL_HEIGHT
            remarks = "" if wall.get("height_mm") else "墙高未从图纸直接识别，使用默认墙高。"
            tables["Walls.csv"].append(
                {
                    "Element_ID": wall_id,
                    "Element_Name": f"墙体{counters['WALL']:03d}",
                    "Project_ID": project_id,
                    "Drawing_ID": drawing_id,
                    "Level_ID": level_id,
                    "Wall_Type": wall.get("recognition_source") or "generic",
                    "Start_X": value_at(start, 0),
                    "Start_Y": value_at(start, 1),
                    "Start_Z": 0,
                    "End_X": value_at(end, 0),
                    "End_Y": value_at(end, 1),
                    "End_Z": 0,
                    "Length": wall.get("length") or line_length(start, end),
                    "Thickness": wall.get("normalized_width") or wall.get("raw_width") or "",
                    "Height": round(height, 3),
                    "Base_Offset": 0,
                    "Top_Offset": "",
                    "Rotation_Angle": angle(start, end),
                    "Grid_Start": "",
                    "Grid_End": "",
                    "Grid_Offset_X": "",
                    "Grid_Offset_Y": "",
                    "Material": "",
                    "Is_Exterior": "",
                    "Confidence": wall.get("confidence", ""),
                    "Source_Geometry_Count": len(wall.get("source_layers", [])) or "",
                    "Needs_Review": bool_text((wall.get("confidence") or 0) < REVIEW_THRESHOLD),
                    "Remarks": remarks,
                }
            )

        for run in sorted(result.get("wall_runs", []), key=lambda w: sort_point(w.get("local_start") or w.get("start"))):
            counters["WALLRUN"] += 1
            wall_run_id = f"WALLRUN-{counters['WALLRUN']:03d}"
            id_maps[("wall_run", drawing_name, str(run.get("id")))] = wall_run_id
            start = point(run.get("local_start") or run.get("start"))
            end = point(run.get("local_end") or run.get("end"))
            source_wall_ids = [
                id_maps.get(("wall", drawing_name, str(source_id)), str(source_id))
                for source_id in list_value(run.get("source_wall_ids"))
            ]
            height = number(run.get("height_mm")) or DEFAULT_WALL_HEIGHT
            tables["Wall_Runs.csv"].append(
                {
                    "Wall_Run_ID": wall_run_id,
                    "Element_Name": f"整墙{counters['WALLRUN']:03d}",
                    "Project_ID": project_id,
                    "Drawing_ID": drawing_id,
                    "Level_ID": level_id,
                    "Start_X": value_at(start, 0),
                    "Start_Y": value_at(start, 1),
                    "Start_Z": 0,
                    "End_X": value_at(end, 0),
                    "End_Y": value_at(end, 1),
                    "End_Z": 0,
                    "Length": run.get("length") or line_length(start, end),
                    "Thickness": run.get("normalized_width", ""),
                    "Height": round(height, 3),
                    "Source_Wall_IDs": ";".join(source_wall_ids),
                    "Source_Wall_Count": run.get("source_wall_count", len(source_wall_ids)),
                    "Opening_IDs": ";".join(str(item) for item in list_value(run.get("opening_ids"))),
                    "Opening_Count": run.get("opening_count", ""),
                    "Rotation_Angle": angle(start, end),
                    "Confidence": run.get("confidence", ""),
                    "Needs_Review": bool_text((run.get("confidence") or 0) < REVIEW_THRESHOLD),
                    "Remarks": run.get("merge_reason", ""),
                }
            )

        for column in sorted(result.get("columns", []), key=lambda c: sort_point(c.get("local_center") or c.get("center"))):
            counters["COLUMN"] += 1
            column_id = f"COLUMN-{counters['COLUMN']:03d}"
            id_maps[("column", drawing_name, str(column.get("id")))] = column_id
            center = point(column.get("local_center") or column.get("center"))
            height = number(column.get("height_mm")) or default_floor_height
            inferred_height = not column.get("height_mm")
            tables["Columns.csv"].append(
                {
                    "Element_ID": column_id,
                    "Element_Name": f"Column {counters['COLUMN']:03d}",
                    "Project_ID": project_id,
                    "Drawing_ID": drawing_id,
                    "Level_ID": level_id,
                    "Column_Type": column.get("column_type") or "unknown",
                    "Center_X": value_at(center, 0),
                    "Center_Y": value_at(center, 1),
                    "Base_Z": 0,
                    "Top_Z": round(height, 3),
                    "Width": column.get("width", ""),
                    "Depth": column.get("depth", ""),
                    "Diameter": column.get("diameter", ""),
                    "Height": round(height, 3),
                    "Rotation_Angle": column.get("rotation_angle", 0),
                    "Grid_Reference": "",
                    "Grid_Offset_X": "",
                    "Grid_Offset_Y": "",
                    "Material": "",
                    "Confidence": column.get("confidence", ""),
                    "Source_Geometry_Count": column.get("source_geometry_count", ""),
                    "Needs_Review": bool_text((column.get("confidence") or 0) < REVIEW_THRESHOLD or inferred_height),
                    "Remarks": "Column height not recognized; using default level height." if inferred_height else "",
                }
            )

        for floor in result.get("floors", []):
            counters["FLOOR"] += 1
            floor_id = f"FLOOR-{counters['FLOOR']:03d}"
            id_maps[("floor", drawing_name, str(floor.get("id")))] = floor_id
            opening_ids = [
                str(item)
                for item in list_value(floor.get("opening_ids"))
                if item not in {None, ""}
            ]
            tables["Floors.csv"].append(
                {
                    "Element_ID": floor_id,
                    "Element_Name": f"Floor {counters['FLOOR']:03d}",
                    "Project_ID": project_id,
                    "Drawing_ID": drawing_id,
                    "Level_ID": level_id,
                    "Floor_Type": floor.get("floor_type") or "default_floor_slab",
                    "Boundary_ID": f"{floor_id}-BOUNDARY",
                    "Boundary_Points": format_points(floor.get("local_boundary_points") or floor.get("boundary_points")),
                    "Area": floor.get("area", ""),
                    "Thickness": floor.get("thickness_mm", ""),
                    "Elevation": floor.get("elevation_mm", 0),
                    "Material": "",
                    "Is_Closed_Boundary": "true",
                    "Opening_IDs": ";".join(opening_ids),
                    "Opening_Count": floor.get("opening_count", len(opening_ids)),
                    "Source": floor.get("source", ""),
                    "Confidence": floor.get("confidence", ""),
                    "Source_Geometry_Count": floor.get("source_geometry_count", ""),
                    "Needs_Review": bool_text(bool(floor.get("needs_review")) or (floor.get("confidence") or 0) < REVIEW_THRESHOLD),
                    "Remarks": floor.get("remarks", ""),
                }
            )

        for parapet in sorted(result.get("parapets", []), key=lambda p: sort_point(p.get("local_start") or p.get("start"))):
            counters["PARAPET"] += 1
            start = point(parapet.get("local_start") or parapet.get("start"))
            end = point(parapet.get("local_end") or parapet.get("end"))
            tables["Parapets.csv"].append(
                {
                    "Element_ID": f"PARAPET-{counters['PARAPET']:03d}",
                    "Element_Name": f"女儿墙{counters['PARAPET']:03d}",
                    "Project_ID": project_id,
                    "Drawing_ID": drawing_id,
                    "Level_ID": level_id,
                    "Host_Roof_ID": "",
                    "Parapet_Type": "dedicated_layer_parapet",
                    "Start_X": value_at(start, 0), "Start_Y": value_at(start, 1), "Start_Z": "",
                    "End_X": value_at(end, 0), "End_Y": value_at(end, 1), "End_Z": "",
                    "Length": parapet.get("length") or line_length(start, end),
                    "Thickness": parapet.get("thickness_mm", ""),
                    "Height": "", "Height_Source": "",
                    "Material": "", "Source": parapet.get("source", "dedicated_parapet_layer"),
                    "Confidence": parapet.get("confidence", ""),
                    "Needs_Review": "true",
                    "Remarks": "Parapet recognized from dedicated CAD layer; height awaits text or elevation inference.",
                }
            )

        for floor_opening in sorted(result.get("floor_openings", []), key=lambda o: sort_point(o.get("local_center") or o.get("center"))):
            counters["FLOOROPENING"] += 1
            opening_id = f"FLOOROPENING-{counters['FLOOROPENING']:03d}"
            raw_floor_id = str(floor_opening.get("host_floor_id") or "")
            host_floor_id = id_maps.get(("floor", drawing_name, raw_floor_id), "")
            center = point(floor_opening.get("local_center") or floor_opening.get("center"))
            confidence = floor_opening.get("confidence") or 0
            tables["Floor_Openings.csv"].append(
                {
                    "Opening_ID": opening_id,
                    "Element_Name": f"Floor Opening {counters['FLOOROPENING']:03d}",
                    "Project_ID": project_id,
                    "Drawing_ID": drawing_id,
                    "Level_ID": level_id,
                    "Host_Floor_ID": host_floor_id,
                    "Opening_Type": floor_opening.get("opening_type") or "rectangular_floor_opening",
                    "Boundary_Points": format_points(floor_opening.get("local_boundary_points") or floor_opening.get("boundary_points")),
                    "Center_X": value_at(center, 0),
                    "Center_Y": value_at(center, 1),
                    "Center_Z": 0,
                    "Width": floor_opening.get("width", ""),
                    "Depth": floor_opening.get("depth", ""),
                    "Area": floor_opening.get("area", ""),
                    "Source": floor_opening.get("source", ""),
                    "Confidence": confidence,
                    "Source_Geometry_Count": floor_opening.get("source_geometry_count", ""),
                    "Needs_Review": bool_text(confidence < REVIEW_THRESHOLD or not host_floor_id),
                    "Remarks": "" if host_floor_id else "Host floor was not inferred.",
                }
            )

        for stair in sorted(expanded_stairs_for_export(result.get("stairs", []), tables), key=lambda s: sort_point(s.get("start"))):
            counters["STAIR"] += 1
            stair_id = f"STAIR-{counters['STAIR']:03d}"
            start = point(stair.get("start"))
            end = point(stair.get("end"))
            confidence = stair.get("confidence") or 0
            tables["Stairs.csv"].append(
                {
                    "Element_ID": stair_id,
                    "Element_Name": f"双跑楼梯{counters['STAIR']:03d}",
                    "Project_ID": project_id,
                    "Drawing_ID": drawing_id,
                    "Level_ID": level_id,
                    "Stair_Type": stair.get("stair_type") or "double_run_stair",
                    "Stair_Core_ID": stair.get("stair_core_id", ""),
                    "Stair_Segment_ID": stair.get("stair_segment_id", ""),
                    "Stair_Segment_Number": stair.get("stair_segment_number", ""),
                    "Level_Span_Count": stair.get("level_span_count", ""),
                    "Start_Level_ID": stair.get("start_level", ""),
                    "End_Level_ID": stair.get("end_level", ""),
                    "Start_X": value_at(start, 0),
                    "Start_Y": value_at(start, 1),
                    "Start_Z": 0,
                    "End_X": value_at(end, 0),
                    "End_Y": value_at(end, 1),
                    "End_Z": stair.get("total_rise_mm", ""),
                    "Boundary_ID": f"{stair_id}-BOUNDARY",
                    "Boundary_Points": format_points(stair.get("boundary_points")),
                    "Stairwell_Opening_ID": f"{stair_id}-OPENING",
                    "Stairwell_Opening_Boundary": format_points(stair.get("stairwell_opening_boundary") or stair.get("boundary_points")),
                    "Opening_Required": bool_text(bool(stair.get("opening_required"))),
                    "Total_Rise": stair.get("total_rise_mm", ""),
                    "Total_Run": stair.get("total_run_mm", ""),
                    "Width": stair.get("width_mm", ""),
                    "Stairwell_Width": stair.get("stairwell_width_mm", ""),
                    "Run_Count": stair.get("run_count", ""),
                    "Risers_Per_Run": stair.get("risers_per_run", ""),
                    "Treads_Per_Run": stair.get("treads_per_run", ""),
                    "Run_Length": stair.get("run_length_mm", ""),
                    "Landing_Length": stair.get("landing_length_mm", ""),
                    "Landing_Width": stair.get("landing_width_mm", ""),
                    "Riser_Height": stair.get("riser_height_mm", ""),
                    "Tread_Depth": stair.get("tread_depth_mm", ""),
                    "Number_Of_Risers": stair.get("number_of_risers", ""),
                    "Number_Of_Treads": stair.get("number_of_treads", ""),
                    "Direction": stair.get("direction", ""),
                    "Confidence": confidence,
                    "Source_Geometry_Count": stair.get("source_segment_count", ""),
                    "Needs_Review": bool_text(bool(stair.get("needs_review")) or confidence < REVIEW_THRESHOLD),
                    "Remarks": stair.get("remarks", ""),
                }
            )
            stair_boundary = stair.get("stairwell_opening_boundary") or stair.get("boundary_points")
            host_floor_id = first_floor_id_for_drawing(tables, drawing_id)
            if stair_boundary:
                pending_stair_openings.append(
                    {
                        "project_id": project_id,
                        "stair_id": stair_id,
                        "drawing_id": drawing_id,
                        "level_id": level_id,
                        "host_floor_id": host_floor_id,
                        "boundary": stair_boundary,
                        "confidence": confidence,
                        "source_geometry_count": stair.get("source_segment_count", ""),
                        "start_level": stair.get("start_level", ""),
                        "end_level": stair.get("end_level", ""),
                        "level_span_count": stair.get("level_span_count", ""),
                    }
                )

        for railing in sorted(result.get("railings", []), key=lambda r: sort_point(r.get("start"))):
            counters["RAILING"] += 1
            railing_id = f"RAILING-{counters['RAILING']:03d}"
            start = point(railing.get("start"))
            end = point(railing.get("end"))
            confidence = railing.get("confidence") or 0
            tables["Railings.csv"].append(
                {
                    "Element_ID": railing_id,
                    "Element_Name": f"Railing {counters['RAILING']:03d}",
                    "Project_ID": project_id,
                    "Drawing_ID": drawing_id,
                    "Level_ID": level_id,
                    "Railing_Type": "stair_railing",
                    "Start_X": value_at(start, 0),
                    "Start_Y": value_at(start, 1),
                    "Start_Z": 0,
                    "End_X": value_at(end, 0),
                    "End_Y": value_at(end, 1),
                    "End_Z": 0,
                    "Height": railing.get("height_mm", ""),
                    "Distance_To_Stairwell": railing.get("distance_to_stairwell_mm", ""),
                    "Related_Stair_ID": railing.get("related_stair_id", ""),
                    "Source": railing.get("source", ""),
                    "Confidence": confidence,
                    "Source_Geometry_Count": railing.get("source_geometry_count", ""),
                    "Needs_Review": bool_text(bool(railing.get("needs_review")) or confidence < REVIEW_THRESHOLD),
                    "Remarks": railing.get("remarks", ""),
                }
            )

        for opening in sorted(result.get("openings", []), key=lambda o: sort_point(o.get("local_point") or o.get("point"))):
            kind = opening.get("kind")
            if kind not in {"door", "window"}:
                continue
            host = id_maps.get(("wall", drawing_name, str(opening.get("host_wall_id"))), "")
            raw_host_run = str(opening.get("host_wall_run_id") or "")
            host_run = id_maps.get(("wall_run", drawing_name, raw_host_run), "")
            center = point(opening.get("local_point") or opening.get("point"))
            confidence = opening.get("confidence") or 0
            missing_host = not (host or host_run)
            mechanical_category = opening.get("component_category") or opening_category_from_source(kind, opening.get("source"))
            category_source = opening_category_source(opening)
            category_confidence = mechanical_category_confidence(opening, mechanical_category)
            classification_input = opening_classification_input(opening, result, mechanical_category, category_source, host, host_run)
            classification_input_json = json.dumps(classification_input, ensure_ascii=False, separators=(",", ":"))
            if kind == "door":
                panel_start = point(opening.get("local_panel_start") or opening.get("panel_start"))
                panel_end = point(opening.get("local_panel_end") or opening.get("panel_end"))
                raw_door_height = number(opening.get("height_mm"))
                door_height_missing = raw_door_height is None
                door_height = raw_door_height if raw_door_height is not None else DEFAULT_DOOR_HEIGHT
                door_height_source = opening.get("height_source", "")
                if door_height_missing:
                    door_height_source = "door_default_2100_needs_review"
                counters["DOOR"] += 1
                door_id = f"DOOR-{counters['DOOR']:03d}"
                id_maps[("opening", drawing_name, str(opening.get("id")))] = door_id
                tables["Doors.csv"].append(
                    {
                        "Element_ID": door_id,
                        "Element_Name": f"门{counters['DOOR']:03d}",
                        "Project_ID": project_id,
                        "Drawing_ID": drawing_id,
                        "Level_ID": level_id,
                        "Host_Wall_ID": host,
                        "Host_Wall_Run_ID": host_run,
                        "Door_Category": mechanical_category,
                        "Final_Category": "",
                        "Mechanical_Category": mechanical_category,
                        "Mechanical_Category_Source": category_source,
                        "Mechanical_Category_Confidence": category_confidence,
                        "Needs_AI_Classification": "true",
                        "Classification_Input": classification_input_json,
                        "Door_Type": opening.get("source") or "unknown",
                        "Door_Mark": opening.get("annotation", ""),
                        "Width": opening.get("width", ""),
                        "Width_Source": opening.get("width_source") or opening.get("size_source", ""),
                        "Height": door_height,
                        "Height_Source": door_height_source,
                        "Thickness": "",
                        "Center_X": value_at(center, 0),
                        "Center_Y": value_at(center, 1),
                        "Center_Z": 0,
                        "Distance_From_Host_Start": "",
                        "Opening_Direction": normalize_open_direction(opening.get("open_direction")),
                        "Swing_Side": normalize_swing_side(opening.get("swing_side")),
                        "Swing_Angle": 90 if opening.get("source") == "quarter_arc" else "",
                        "Swing_Source": opening.get("swing_source", ""),
                        "Swing_Confidence": opening.get("swing_confidence", ""),
                        "Panel_Start_X": value_at(panel_start, 0),
                        "Panel_Start_Y": value_at(panel_start, 1),
                        "Panel_End_X": value_at(panel_end, 0),
                        "Panel_End_Y": value_at(panel_end, 1),
                        "Panel_Thickness": opening.get("panel_thickness_mm", ""),
                        "Panel_Wall_Angle": opening.get("panel_wall_angle_deg", ""),
                        "Matched_Elevation_Drawing": opening.get("matched_elevation_drawing", ""),
                        "Matched_Elevation_Door_ID": opening.get("matched_elevation_opening_id", ""),
                        "Cross_View_Match_Score": opening.get("cross_view_match_score", ""),
                        "Cross_View_Match_Status": opening.get("cross_view_match_status", ""),
                        "Grid_Reference": "",
                        "Grid_Offset_X": "",
                        "Grid_Offset_Y": "",
                        "Confidence": confidence,
                        "Source_Geometry_Count": 1,
                        "Needs_Review": bool_text(confidence < REVIEW_THRESHOLD or missing_host or door_height_missing),
                        "Remarks": review_reason(missing_host, None if door_height_missing else door_height),
                    }
                )
                append_opening_wall_run_map(tables, project_id, drawing_id, door_id, "door", raw_host_run, host_run, host, center, result, confidence)
            else:
                counters["WINDOW"] += 1
                window_id = f"WINDOW-{counters['WINDOW']:03d}"
                id_maps[("opening", drawing_name, str(opening.get("id")))] = window_id
                tables["Windows.csv"].append(
                    {
                        "Element_ID": window_id,
                        "Element_Name": f"窗{counters['WINDOW']:03d}",
                        "Project_ID": project_id,
                        "Drawing_ID": drawing_id,
                        "Level_ID": level_id,
                        "Host_Wall_ID": host,
                        "Host_Wall_Run_ID": host_run,
                        "Window_Category": mechanical_category,
                        "Final_Category": "",
                        "Mechanical_Category": mechanical_category,
                        "Mechanical_Category_Source": category_source,
                        "Mechanical_Category_Confidence": category_confidence,
                        "Needs_AI_Classification": "true",
                        "Classification_Input": classification_input_json,
                        "Window_Type": opening.get("source") or "unknown",
                        "Width": opening.get("width", ""),
                        "Width_Source": opening.get("width_source") or opening.get("size_source", ""),
                        "Height": opening.get("height_mm", ""),
                        "Height_Source": opening.get("height_source", ""),
                        "Sill_Height": opening.get("sill_height_mm", ""),
                        "Sill_Height_Source": opening.get("sill_height_source", ""),
                        "Matched_Elevation_Drawing": opening.get("matched_elevation_drawing", ""),
                        "Matched_Elevation_Window_ID": opening.get("matched_elevation_opening_id", ""),
                        "Cross_View_Match_Score": opening.get("cross_view_match_score", ""),
                        "Center_X": value_at(center, 0),
                        "Center_Y": value_at(center, 1),
                        "Center_Z": 0,
                        "Distance_From_Host_Start": "",
                        "Grid_Reference": "",
                        "Grid_Offset_X": "",
                        "Grid_Offset_Y": "",
                        "Confidence": confidence,
                        "Source_Geometry_Count": 1,
                        "Needs_Review": bool_text(confidence < REVIEW_THRESHOLD or missing_host or not opening.get("height_mm")),
                        "Remarks": review_reason(missing_host, opening.get("height_mm")),
                    }
                )
                append_opening_wall_run_map(tables, project_id, drawing_id, window_id, "window", raw_host_run, host_run, host, center, result, confidence)

        for item in result.get("plan_summary", {}).get("elevation_marks", []):
            counters["DIM"] += 1
            tables["Dimensions.csv"].append(
                {
                    "Dimension_ID": f"DIM-{counters['DIM']:03d}",
                    "Project_ID": project_id,
                    "Drawing_ID": drawing_id,
                    "Level_ID": level_id,
                    "Dimension_Type": "elevation_mark",
                    "Value": item.get("elevation_mm", item.get("value", "")),
                    "Unit": UNIT,
                    "Start_X": "",
                    "Start_Y": "",
                    "End_X": "",
                    "End_Y": "",
                    "Associated_Element_ID": "",
                    "Confidence": item.get("confidence", ""),
                    "Needs_Review": "false",
                    "Remarks": item.get("label", ""),
                }
            )

        for text in result.get("notes", {}).get("text_items", []):
            counters["TEXT"] += 1
            text_id = f"TEXT-{counters['TEXT']:03d}"
            original = point(text.get("point"))
            tables["Text_Annotations.csv"].append(
                {
                    "Text_ID": text_id,
                    "Project_ID": project_id,
                    "Drawing_ID": drawing_id,
                    "Level_ID": level_id,
                    "Text_Content": text.get("text", ""),
                    "Original_X": value_at(original, 0),
                    "Original_Y": value_at(original, 1),
                    "Original_Z": 0,
                    "Local_X": value_at(original, 0),
                    "Local_Y": value_at(original, 1),
                    "Local_Z": 0,
                    "Layer": text.get("layer", ""),
                    "Height": text.get("height", ""),
                    "Rotation_Angle": text.get("rotation", ""),
                    "Associated_Element_ID": "",
                    "Associated_Element_Type": "",
                    "Association_Distance": "",
                    "Association_Method": "",
                    "Confidence": 0.9,
                    "Needs_Review": "false",
                    "Remarks": "",
                }
            )

        for raw in result.get("raw_geometry", []):
            counters["RAW"] += 1
            raw_id = f"RAW-{counters['RAW']:06d}"
            tables["Raw_Geometry.csv"].append(
                {
                    "Raw_Geometry_ID": raw_id,
                    "Project_ID": project_id,
                    "Drawing_ID": drawing_id,
                    "Layer": raw.get("layer", ""),
                    "Geometry_Type": raw.get("raw_type", ""),
                    "Start_X": raw.get("start_x", ""),
                    "Start_Y": raw.get("start_y", ""),
                    "End_X": raw.get("end_x", ""),
                    "End_Y": raw.get("end_y", ""),
                    "Center_X": raw.get("center_x", ""),
                    "Center_Y": raw.get("center_y", ""),
                    "Radius": raw.get("radius", ""),
                    "Text_Content": raw.get("text", ""),
                    "Block_Name": raw.get("block_name", ""),
                    "Point_Count": raw.get("point_count", ""),
                    "Classified_As": "",
                    "Associated_Element_ID": "",
                    "Confidence": "",
                    "Needs_Review": "false",
                    "Remarks": "",
                }
            )
    append_stairwell_openings(project_id, tables, counters, pending_stair_openings)
    add_derived_roof_and_parapets(project_id, tables)
    enrich_text_annotation_associations(tables)
    return tables


def write_summary_report(path: Path, project_id: str, project_name: str, drawing_results: list[tuple[str, dict]], tables: dict[str, list[dict[str, Any]]], now: datetime) -> None:
    lines = [
        "# 构件识别汇总",
        "",
        "## 项目基本信息",
        "",
        f"- 项目名称：{project_name}",
        f"- 项目ID：{project_id}",
        f"- 识别时间：{now.isoformat(timespec='seconds')}",
        f"- 图纸数量：{len(drawing_results)}",
        f"- 坐标单位：{UNIT}",
        f"- 输出版本：{EXPORTER_VERSION}",
        "",
        "## 图纸识别总体统计",
        "",
        f"- 轴网数量：{len(tables['Grids.csv'])}",
        f"- 墙体数量：{len(tables['Walls.csv'])}",
        f"- 门数量：{len(tables['Doors.csv'])}",
        f"- 窗数量：{len(tables['Windows.csv'])}",
        f"- 楼梯数量：{len(tables['Stairs.csv'])}",
        f"- 栏杆数量：{len(tables['Railings.csv'])}",
        f"- 原始图元数量：{len(tables['Raw_Geometry.csv'])}",
        f"- 需要人工复核：{len(tables['Uncertain_Elements.csv'])}",
        "",
        "## 按图纸汇总",
        "",
    ]
    for drawing in tables["Drawings.csv"]:
        drawing_id = drawing["Drawing_ID"]
        lines.append(f"### {drawing_id}：{drawing['Drawing_Name']}")
        lines.append(f"- 图纸类型：{drawing['Drawing_Type']}")
        lines.append(f"- 墙体：{count_by(tables['Walls.csv'], 'Drawing_ID', drawing_id)}")
        lines.append(f"- 门：{count_by(tables['Doors.csv'], 'Drawing_ID', drawing_id)}")
        lines.append(f"- 窗：{count_by(tables['Windows.csv'], 'Drawing_ID', drawing_id)}")
        lines.append(f"- 楼梯：{count_by(tables['Stairs.csv'], 'Drawing_ID', drawing_id)}")
        lines.append(f"- 栏杆：{count_by(tables['Railings.csv'], 'Drawing_ID', drawing_id)}")
        lines.append("")
    append_element_summary(lines, "墙体汇总", tables["Walls.csv"], "Element_ID", ["Length", "Thickness", "Height", "Confidence", "Needs_Review"])
    append_element_summary(lines, "门汇总", tables["Doors.csv"], "Element_ID", ["Host_Wall_ID", "Width", "Height", "Confidence", "Needs_Review"])
    append_element_summary(lines, "窗汇总", tables["Windows.csv"], "Element_ID", ["Host_Wall_ID", "Width", "Height", "Sill_Height", "Confidence", "Needs_Review"])
    append_element_summary(lines, "楼梯汇总", tables["Stairs.csv"], "Element_ID", ["Total_Rise", "Riser_Height", "Tread_Depth", "Number_Of_Risers", "Risers_Per_Run", "Landing_Length", "Landing_Width", "Stairwell_Width", "Confidence", "Needs_Review"])
    append_element_summary(lines, "栏杆汇总", tables["Railings.csv"], "Element_ID", ["Height", "Distance_To_Stairwell", "Related_Stair_ID", "Confidence", "Needs_Review"])
    append_element_summary(lines, "需要人工复核的内容", tables["Uncertain_Elements.csv"], "Element_ID", ["Element_Type", "Reason", "Confidence"])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_detailed_report(path: Path, project_id: str, project_name: str, drawing_results: list[tuple[str, dict]], tables: dict[str, list[dict[str, Any]]], validation_errors: list[dict[str, Any]], now: datetime) -> None:
    lines = [
        "# 详细识别报告",
        "",
        f"- 项目：{project_name}（{project_id}）",
        f"- 生成时间：{now.isoformat(timespec='seconds')}",
        "",
        "## 统一数据模型说明",
        "",
        "本报告由同一套标准化数据模型生成，汇总报告、CSV 和详细报告中的构件 ID 保持一致。",
        "",
        "## 原始图元统计",
        "",
    ]
    for drawing in tables["Drawings.csv"]:
        drawing_id = drawing["Drawing_ID"]
        raws = [row for row in tables["Raw_Geometry.csv"] if row.get("Drawing_ID") == drawing_id]
        lines.append(f"### {drawing_id}：{drawing['Drawing_Name']}")
        lines.append(f"- 原始图元总数：{len(raws)}")
        for geom_type in sorted({row.get("Geometry_Type") for row in raws if row.get("Geometry_Type")}):
            lines.append(f"- {geom_type}：{count_by(raws, 'Geometry_Type', geom_type)}")
        lines.append("")
    lines.extend(["## 构件识别依据", ""])
    for wall in tables["Walls.csv"]:
        lines.append(f"### 墙体 {wall['Element_ID']}")
        lines.append(f"- 起点：({wall['Start_X']}, {wall['Start_Y']}, {wall['Start_Z']})")
        lines.append(f"- 终点：({wall['End_X']}, {wall['End_Y']}, {wall['End_Z']})")
        lines.append(f"- 厚度：{wall['Thickness']} mm")
        lines.append(f"- 识别置信度：{wall['Confidence']}")
        lines.append(f"- 备注：{wall['Remarks']}")
        lines.append("")
    for door in tables["Doors.csv"]:
        lines.append(f"### 门 {door['Element_ID']}")
        lines.append(f"- 所属墙体：{door['Host_Wall_ID']}")
        lines.append(f"- 中心点：({door['Center_X']}, {door['Center_Y']}, {door['Center_Z']})")
        lines.append(f"- 尺寸：宽 {door['Width']} mm，高 {door['Height']} mm")
        lines.append(f"- 需要复核：{door['Needs_Review']}")
        lines.append(f"- 备注：{door['Remarks']}")
        lines.append("")
    for window in tables["Windows.csv"]:
        lines.append(f"### 窗 {window['Element_ID']}")
        lines.append(f"- 所属墙体：{window['Host_Wall_ID']}")
        lines.append(f"- 中心点：({window['Center_X']}, {window['Center_Y']}, {window['Center_Z']})")
        lines.append(f"- 尺寸：宽 {window['Width']} mm，高 {window['Height']} mm")
        lines.append(f"- 窗台高度：{window['Sill_Height']}")
        lines.append(f"- 需要复核：{window['Needs_Review']}")
        lines.append(f"- 备注：{window['Remarks']}")
        lines.append("")
    for stair in tables["Stairs.csv"]:
        lines.append(f"### 楼梯 {stair['Element_ID']}")
        lines.append(f"- 类型：{stair['Stair_Type']}")
        lines.append(f"- 起点：({stair['Start_X']}, {stair['Start_Y']}, {stair['Start_Z']})")
        lines.append(f"- 终点：({stair['End_X']}, {stair['End_Y']}, {stair['End_Z']})")
        lines.append(f"- 总高：{stair['Total_Rise']} mm")
        lines.append(f"- 总跑长：{stair['Total_Run']} mm")
        lines.append(f"- 梯段/平台宽：{stair['Width']} mm")
        lines.append(f"- 梯井宽度：{stair['Stairwell_Width']} mm")
        lines.append(f"- 跑数：{stair['Run_Count']}")
        lines.append(f"- 每跑级数：{stair['Risers_Per_Run']}")
        lines.append(f"- 每跑踏步数：{stair['Treads_Per_Run']}")
        lines.append(f"- 单跑长度：{stair['Run_Length']} mm")
        lines.append(f"- 平台尺寸：{stair['Landing_Length']} x {stair['Landing_Width']} mm")
        lines.append(f"- 踏步高/宽：{stair['Riser_Height']} / {stair['Tread_Depth']} mm")
        lines.append(f"- 总级数/总踏步数：{stair['Number_Of_Risers']} / {stair['Number_Of_Treads']}")
        lines.append(f"- 楼梯洞口边界：{stair['Stairwell_Opening_Boundary']}")
        lines.append(f"- 来源线数量：{stair['Source_Geometry_Count']}")
        lines.append(f"- 需要复核：{stair['Needs_Review']}")
        lines.append(f"- 备注：{stair['Remarks']}")
        lines.append("")
    for railing in tables["Railings.csv"]:
        lines.append(f"### 栏杆 {railing['Element_ID']}")
        lines.append(f"- 起点：({railing['Start_X']}, {railing['Start_Y']}, {railing['Start_Z']})")
        lines.append(f"- 终点：({railing['End_X']}, {railing['End_Y']}, {railing['End_Z']})")
        lines.append(f"- 高度：{railing['Height']} mm")
        lines.append(f"- 到梯井距离：{railing['Distance_To_Stairwell']} mm")
        lines.append(f"- 关联楼梯：{railing['Related_Stair_ID']}")
        lines.append(f"- 识别来源：{railing['Source']}")
        lines.append(f"- 来源线数量：{railing['Source_Geometry_Count']}")
        lines.append(f"- 需要复核：{railing['Needs_Review']}")
        lines.append(f"- 备注：{railing['Remarks']}")
        lines.append("")
    lines.extend(["## 数据验证结果", ""])
    if validation_errors:
        for error in validation_errors:
            lines.append(f"- [{error['Severity']}] {error['File_Name']} / {error['Record_ID']} / {error['Field_Name']}：{error['Error_Message']}")
    else:
        lines.append("- 未发现严重数据验证问题。")
    lines.extend(["", "## 推断值和默认值", ""])
    inferred = [row for row in tables["Walls.csv"] if "默认墙高" in str(row.get("Remarks", ""))]
    if inferred:
        for row in inferred:
            lines.append(f"- {row['Element_ID']} 使用默认墙高 {row['Height']} mm。")
    else:
        lines.append("- 未记录默认墙高。")
    path.write_text("\n".join(lines), encoding="utf-8")


def validate_tables(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    ids: dict[str, str] = {}
    for file_name, rows in tables.items():
        id_fields = [field for field in CSV_SCHEMAS.get(file_name, []) if field.endswith("_ID") or field == "Element_ID"]
        primary = id_fields[0] if id_fields else ""
        for row in rows:
            record_id = str(row.get(primary, "")) if primary else ""
            if primary and not record_id:
                errors.append(error_row(len(errors) + 1, "error", file_name, record_id, primary, "missing_id", "ID 字段为空。", "检查标准化导出映射。"))
            if record_id:
                key = f"{file_name}:{record_id}"
                if key in ids:
                    errors.append(error_row(len(errors) + 1, "error", file_name, record_id, primary, "duplicate_id", "ID 重复。", "保持同一文件内 ID 唯一。"))
                ids[key] = file_name
            for key, value in row.items():
                if key.endswith("_X") or key.endswith("_Y") or key.endswith("_Z") or key in {"Length", "Width", "Stairwell_Width", "Height", "Thickness", "Elevation"}:
                    if value == "" or value is None:
                        continue
                    if not valid_number(value):
                        errors.append(error_row(len(errors) + 1, "warning", file_name, record_id, key, "invalid_number", "数值字段不是有效数字。", "检查坐标或尺寸来源。"))

    wall_ids = {row["Element_ID"] for row in tables.get("Walls.csv", [])}
    drawing_types = {
        str(row.get("Drawing_ID", "")): str(row.get("Drawing_Type", "")).strip().lower()
        for row in tables.get("Drawings.csv", [])
    }
    for file_name in ["Doors.csv", "Windows.csv"]:
        for row in tables.get(file_name, []):
            drawing_type = drawing_types.get(str(row.get("Drawing_ID", "")), "")
            if drawing_type and drawing_type != "floor_plan":
                continue
            host = row.get("Host_Wall_ID")
            if not host:
                errors.append(error_row(len(errors) + 1, "warning", file_name, row.get("Element_ID", ""), "Host_Wall_ID", "missing_reference", "门窗没有关联墙体。", "人工复核门窗所属墙体。"))
            elif host not in wall_ids:
                errors.append(error_row(len(errors) + 1, "error", file_name, row.get("Element_ID", ""), "Host_Wall_ID", "broken_reference", "引用的墙体 ID 不存在。", "检查墙体 ID 映射。"))
    return errors


def add_derived_roof_and_parapets(project_id: str, tables: dict[str, list[dict[str, Any]]]) -> None:
    source_floor = select_roof_source_floor(tables)
    if source_floor is None:
        return
    boundary = parse_point_pairs(source_floor.get("Boundary_Points"))
    if len(boundary) < 3:
        return

    drawing_id = source_floor.get("Drawing_ID", "")
    level_id = source_floor.get("Level_ID", "")
    roof_id = f"ROOF-{len(tables['Roofs.csv']) + 1:03d}"
    source = roof_source_label(tables, drawing_id)
    confidence = 0.76 if source == "roof_plan_outer_boundary" else 0.68
    elevation = roof_elevation(tables, level_id, source_floor.get("Elevation"))
    parapet_height, parapet_height_source = infer_parapet_height(tables, elevation)
    parapet_height_inferred = parapet_height is not None
    if parapet_height is None:
        parapet_height = 900
        parapet_height_source = "default_review_required"
    parapet_confidence = 0.82 if parapet_height_inferred else confidence
    tables["Roofs.csv"].append(
        {
            "Element_ID": roof_id,
            "Element_Name": "平屋面001",
            "Project_ID": project_id,
            "Drawing_ID": drawing_id,
            "Level_ID": level_id,
            "Roof_Type": "flat_roof",
            "Boundary_ID": f"{roof_id}-BOUNDARY",
            "Boundary_Points": format_points(boundary),
            "Area": round(abs(polygon_area_from_pairs(boundary)), 3),
            "Thickness": source_floor.get("Thickness", ""),
            "Elevation": elevation,
            "Slope": 0,
            "Drainage_Type": "",
            "Material": "",
            "Source": source,
            "Confidence": confidence,
            "Needs_Review": bool_text(confidence < REVIEW_THRESHOLD),
            "Remarks": "Flat roof boundary inferred from the top plan outer wall/floor boundary.",
        }
    )

    dedicated_parapets = [row for row in tables["Parapets.csv"] if row.get("Drawing_ID") == drawing_id and row.get("Parapet_Type") == "dedicated_layer_parapet"]
    if dedicated_parapets:
        for row in dedicated_parapets:
            row["Host_Roof_ID"] = roof_id
            row["Start_Z"] = elevation
            row["End_Z"] = elevation
            row["Height"] = round(parapet_height, 3)
            row["Height_Source"] = parapet_height_source
            row["Confidence"] = max(number(row.get("Confidence")) or 0.0, parapet_confidence)
            row["Needs_Review"] = bool_text(parapet_confidence < REVIEW_THRESHOLD or not parapet_height_inferred)
            row["Remarks"] = "Parapet geometry recognized from dedicated CAD layer. " + parapet_height_remarks(parapet_height_source, parapet_height_inferred)
        return

    closed = boundary + [boundary[0]]
    for index, (start, end) in enumerate(zip(closed, closed[1:]), start=1):
        tables["Parapets.csv"].append(
            {
                "Element_ID": f"PARAPET-{len(tables['Parapets.csv']) + 1:03d}",
                "Element_Name": f"女儿墙{index:03d}",
                "Project_ID": project_id,
                "Drawing_ID": drawing_id,
                "Level_ID": level_id,
                "Host_Roof_ID": roof_id,
                "Parapet_Type": "roof_edge_parapet",
                "Start_X": round(start[0], 3),
                "Start_Y": round(start[1], 3),
                "Start_Z": elevation,
                "End_X": round(end[0], 3),
                "End_Y": round(end[1], 3),
                "End_Z": elevation,
                "Length": line_length(start, end),
                "Thickness": 200,
                "Height": round(parapet_height, 3),
                "Height_Source": parapet_height_source,
                "Material": "",
                "Source": source,
                "Confidence": parapet_confidence,
                "Needs_Review": bool_text(parapet_confidence < REVIEW_THRESHOLD or not parapet_height_inferred),
                "Remarks": parapet_height_remarks(parapet_height_source, parapet_height_inferred),
            }
        )


def parapet_height_remarks(height_source: str, height_inferred: bool) -> str:
    if not height_inferred:
        return "Parapet inferred along flat roof boundary; height/thickness are defaults for review."
    if height_source.startswith("elevation_mark_difference"):
        return "Parapet height inferred from elevation top mark minus roof/base level; thickness is a review default."
    return "Parapet height recognized from CAD text; thickness is a review default."


def infer_parapet_height(tables: dict[str, list[dict[str, Any]]], base_elevation: object = None) -> tuple[float | None, str]:
    candidates: list[tuple[float, str]] = []
    for row in tables.get("Text_Annotations.csv", []):
        text = str(row.get("Text_Content") or "")
        height = parse_parapet_height_text(text)
        if height is None:
            continue
        candidates.append((height, f"text_annotation:{text[:40]}"))
    if not candidates:
        return infer_parapet_height_from_elevation_marks(tables, base_elevation)
    counts: dict[float, tuple[int, str]] = {}
    for height, source in candidates:
        key = round(height, 1)
        count, first_source = counts.get(key, (0, source))
        counts[key] = (count + 1, first_source)
    best_height, (_count, source) = max(counts.items(), key=lambda item: (item[1][0], item[0]))
    return best_height, source


def infer_parapet_height_from_elevation_marks(tables: dict[str, list[dict[str, Any]]], base_elevation: object) -> tuple[float | None, str]:
    base = number(base_elevation)
    if base is None:
        return None, ""
    elevations: list[float] = []
    for row in tables.get("Dimensions.csv", []):
        if str(row.get("Dimension_Type") or "") != "elevation_mark":
            continue
        value = number(row.get("Value"))
        if value is not None:
            elevations.append(value)
    top = max((value for value in elevations if value > base + 1), default=None)
    if top is None:
        return None, ""
    height = top - base
    if not 250 <= height <= 6000:
        return None, ""
    source = f"elevation_mark_difference:top={top:g};base={base:g}"
    return round(height, 3), source


def parse_parapet_height_text(text: str) -> float | None:
    if not re.search(r"\u5973\u513f\u5899|parapet", text, re.I):
        return None
    normalized = text.replace("\uff1a", ":").replace("\uff1d", "=").replace("\uff0c", ",")
    patterns = [
        r"(?:\u5973\u513f\u5899|parapet)[^\d+\-]{0,12}(?:\u9ad8\u5ea6|\u9ad8|H|h|height)\s*[:=]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|m|\u7c73)?",
        r"(?:\u9ad8\u5ea6|\u9ad8|H|h|height)\s*[:=]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|m|\u7c73)?[^\d+\-]{0,12}(?:\u5973\u513f\u5899|parapet)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, re.I)
        if not match:
            continue
        value = float(match.group("value"))
        unit = (match.group("unit") or "").lower()
        if unit in {"m", "\u7c73"} or (0.2 <= value <= 3.0 and unit != "mm"):
            value *= 1000.0
        if 250 <= value <= 1800:
            return round(value, 3)
    return None


def select_roof_source_floor(tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    floors = tables.get("Floors.csv", [])
    if not floors:
        return None
    drawings = {row.get("Drawing_ID"): row for row in tables.get("Drawings.csv", [])}
    levels = {row.get("Level_ID"): row for row in tables.get("Levels.csv", [])}

    def score(floor: dict[str, Any]) -> tuple[int, float, str]:
        drawing = drawings.get(floor.get("Drawing_ID"), {})
        level = levels.get(floor.get("Level_ID"), {})
        drawing_name = str(drawing.get("Drawing_Name") or "")
        is_roof_name = bool(re.search(r"屋顶|屋面|roof", drawing_name, re.I))
        level_number = number(level.get("Level_Number"))
        elevation = number(level.get("Elevation")) or number(floor.get("Elevation")) or 0.0
        return (1 if is_roof_name else 0, level_number if level_number is not None else -9999.0, elevation)

    return max(floors, key=score)


def roof_source_label(tables: dict[str, list[dict[str, Any]]], drawing_id: object) -> str:
    drawing = next((row for row in tables.get("Drawings.csv", []) if row.get("Drawing_ID") == drawing_id), {})
    name = str(drawing.get("Drawing_Name") or "")
    if re.search(r"屋顶|屋面|roof", name, re.I):
        return "roof_plan_outer_boundary"
    return "top_floor_outer_boundary"


def roof_elevation(tables: dict[str, list[dict[str, Any]]], level_id: object, fallback: object) -> Any:
    level = next((row for row in tables.get("Levels.csv", []) if row.get("Level_ID") == level_id), {})
    value = number(level.get("Elevation"))
    if value is None:
        value = number(fallback)
    return round(value or 0.0, 3)


def parse_point_pairs(value: object) -> list[tuple[float, float]]:
    if not value:
        return []
    raw = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    pairs: list[tuple[float, float]] = []
    for item in raw:
        pt = point(item)
        if pt is not None:
            pairs.append(pt)
        elif isinstance(item, dict):
            x = number(item.get("x"))
            y = number(item.get("y"))
            if x is not None and y is not None:
                pairs.append((x, y))
    return pairs


def polygon_area_from_pairs(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    for index, current in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        area += current[0] * nxt[1] - nxt[0] * current[1]
    return area / 2


def bbox_from_pairs(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def enrich_text_annotation_associations(tables: dict[str, list[dict[str, Any]]]) -> None:
    element_index = build_text_association_element_index(tables)
    for text in tables.get("Text_Annotations.csv", []):
        point_xy = row_point(text, "Local")
        if point_xy is None:
            continue
        drawing_id = str(text.get("Drawing_ID", ""))
        best = nearest_element_reference(point_xy, element_index.get(drawing_id, []))
        if best is None:
            continue
        text["Associated_Element_ID"] = best["element_id"]
        text["Associated_Element_Type"] = best["element_type"]
        text["Association_Distance"] = round(best["distance"], 3)
        text["Association_Method"] = best["method"]
        text["Remarks"] = "Nearest recognized element; available for space and material semantic completion."


def build_text_association_element_index(tables: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    specs = [
        ("Grids.csv", "grid", "Grid_ID", "line", "Start", "End"),
        ("Walls.csv", "wall", "Element_ID", "line", "Start", "End"),
        ("Wall_Runs.csv", "wall_run", "Wall_Run_ID", "line", "Start", "End"),
        ("Doors.csv", "door", "Element_ID", "point", "Center", ""),
        ("Windows.csv", "window", "Element_ID", "point", "Center", ""),
        ("Columns.csv", "column", "Element_ID", "point", "Center", ""),
        ("Floors.csv", "floor", "Element_ID", "polygon", "Boundary_Points", ""),
        ("Roofs.csv", "roof", "Element_ID", "polygon", "Boundary_Points", ""),
        ("Parapets.csv", "parapet", "Element_ID", "line", "Start", "End"),
        ("Floor_Openings.csv", "floor_opening", "Opening_ID", "point", "Center", ""),
        ("Stairs.csv", "stair", "Element_ID", "line", "Start", "End"),
        ("Railings.csv", "railing", "Element_ID", "line", "Start", "End"),
    ]
    indexed: dict[str, list[dict[str, Any]]] = {}
    for file_name, element_type, id_field, geometry_type, first_key, second_key in specs:
        for row in tables.get(file_name, []):
            drawing_id = str(row.get("Drawing_ID", ""))
            element_id = str(row.get(id_field) or "")
            if not drawing_id or not element_id:
                continue
            geometry = element_association_geometry(row, geometry_type, first_key, second_key)
            if geometry is None:
                continue
            indexed.setdefault(drawing_id, []).append(
                {
                    "element_id": element_id,
                    "element_type": element_type,
                    "geometry_type": geometry_type,
                    "geometry": geometry,
                }
            )
    return indexed


def element_association_geometry(row: dict[str, Any], geometry_type: str, first_key: str, second_key: str) -> Any:
    if geometry_type == "point":
        return row_point(row, first_key)
    if geometry_type == "line":
        start = row_point(row, first_key)
        end = row_point(row, second_key)
        return (start, end) if start and end else None
    if geometry_type == "polygon":
        points = parse_point_pairs(row.get(first_key))
        return points if points else None
    return None


def nearest_element_reference(point_xy: tuple[float, float], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for candidate in candidates:
        distance_value = distance_to_candidate(point_xy, candidate)
        if distance_value is None:
            continue
        if best is None or distance_value < best["distance"]:
            best = {
                "element_id": candidate["element_id"],
                "element_type": candidate["element_type"],
                "distance": distance_value,
                "method": f"nearest_{candidate['geometry_type']}",
            }
    if best is None or best["distance"] > 5000:
        return None
    return best


def distance_to_candidate(point_xy: tuple[float, float], candidate: dict[str, Any]) -> float | None:
    geometry_type = candidate.get("geometry_type")
    geometry = candidate.get("geometry")
    if geometry_type == "point":
        return point_distance(point_xy, geometry)
    if geometry_type == "line":
        start, end = geometry
        return point_to_segment_distance_2d(point_xy, start, end)
    if geometry_type == "polygon":
        points = geometry
        if not points:
            return None
        edges = list(zip(points, points[1:] + points[:1])) if len(points) > 1 else []
        if edges:
            return min(point_to_segment_distance_2d(point_xy, a, b) for a, b in edges)
        return point_distance(point_xy, points[0])
    return None


def row_point(row: dict[str, Any], prefix: str) -> tuple[float, float] | None:
    x = number(row.get(f"{prefix}_X"))
    y = number(row.get(f"{prefix}_Y"))
    if x is None or y is None:
        return None
    return x, y


def point_distance(a: tuple[float, float], b: tuple[float, float] | None) -> float | None:
    if b is None:
        return None
    return hypot(a[0] - b[0], a[1] - b[1])


def point_to_segment_distance_2d(
    point_xy: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    ab = (end[0] - start[0], end[1] - start[1])
    ap = (point_xy[0] - start[0], point_xy[1] - start[1])
    length_sq = ab[0] ** 2 + ab[1] ** 2
    if length_sq <= 0:
        return hypot(point_xy[0] - start[0], point_xy[1] - start[1])
    t = max(0.0, min(1.0, (ap[0] * ab[0] + ap[1] * ab[1]) / length_sq))
    projection = (start[0] + ab[0] * t, start[1] + ab[1] * t)
    return hypot(point_xy[0] - projection[0], point_xy[1] - projection[1])


def first_floor_id_for_drawing(tables: dict[str, list[dict[str, Any]]], drawing_id: object) -> str:
    floor = next((row for row in tables.get("Floors.csv", []) if row.get("Drawing_ID") == drawing_id), None)
    return str(floor.get("Element_ID") or "") if floor else ""


def expanded_stairs_for_export(stairs: list[dict], tables: dict[str, list[dict[str, Any]]]) -> list[dict]:
    result: list[dict] = []
    for stair in stairs:
        result.extend(stair_level_segments(stair, tables))
    return result


def stair_level_segments(stair: dict, tables: dict[str, list[dict[str, Any]]]) -> list[dict]:
    span_count = inferred_stair_level_span_count(stair, tables)
    if span_count <= 1:
        copied = dict(stair)
        copied.setdefault("stair_core_id", copied.get("id") or copied.get("element_id") or "")
        copied.setdefault("level_span_count", 1)
        return [copied]

    levels = ordered_numbered_levels(tables)
    if len(levels) < span_count + 1:
        copied = dict(stair)
        copied.setdefault("stair_core_id", copied.get("id") or copied.get("element_id") or "")
        copied.setdefault("level_span_count", span_count)
        return [copied]

    total_rise = number(stair.get("total_rise_mm"))
    total_risers = number(stair.get("number_of_risers"))
    total_treads = number(stair.get("number_of_treads"))
    total_run = number(stair.get("total_run_mm"))
    core_id = str(stair.get("stair_core_id") or stair.get("id") or stair.get("element_id") or "STAIR")
    segments: list[dict] = []
    for index in range(span_count):
        segment = dict(stair)
        segment["stair_core_id"] = core_id
        segment["stair_segment_number"] = index + 1
        segment["stair_segment_id"] = f"{core_id}-SEG{index + 1:02d}"
        segment["level_span_count"] = span_count
        segment["start_level"] = levels[index].get("Level_ID") or levels[index].get("Level_Name") or ""
        segment["end_level"] = levels[index + 1].get("Level_ID") or levels[index + 1].get("Level_Name") or ""
        if total_rise:
            segment["total_rise_mm"] = round(total_rise / span_count, 3)
        if total_risers:
            segment["number_of_risers"] = round(total_risers / span_count)
        if total_treads:
            segment["number_of_treads"] = round(total_treads / span_count)
        run_count = int(segment.get("run_count") or 2)
        if segment.get("number_of_risers"):
            segment["risers_per_run"] = round(float(segment["number_of_risers"]) / run_count) if run_count else None
        if segment.get("number_of_treads"):
            segment["treads_per_run"] = round(float(segment["number_of_treads"]) / run_count) if run_count else None
        tread_depth = number(segment.get("tread_depth_mm"))
        if tread_depth and segment.get("treads_per_run"):
            segment["run_length_mm"] = round(tread_depth * float(segment["treads_per_run"]), 3)
        elif total_run:
            segment["total_run_mm"] = round(total_run / span_count, 3)
        if segment.get("run_length_mm"):
            landing_width = number(segment.get("landing_width_mm")) or 0.0
            segment["total_run_mm"] = round(float(segment["run_length_mm"]) * run_count + landing_width, 3)
        segment["remarks"] = (
            str(segment.get("remarks") or "")
            + f" 已按相邻楼层拆分为第{index + 1}/{span_count}段；本段保持双跑楼梯。"
        ).strip()
        segments.append(segment)
    return segments


def inferred_stair_level_span_count(stair: dict, tables: dict[str, list[dict[str, Any]]]) -> int:
    explicit = int(number(stair.get("level_span_count")) or 0)
    if explicit > 1:
        return explicit
    start_number = level_number_from_label(stair.get("start_level"))
    end_number = level_number_from_label(stair.get("end_level"))
    if start_number is not None and end_number is not None and end_number > start_number:
        return end_number - start_number
    total_rise = number(stair.get("total_rise_mm"))
    if not total_rise:
        return 1
    floor_height = common_level_floor_height(tables)
    if not floor_height:
        return 1
    span = round(total_rise / floor_height)
    if span <= 1 or span > 8:
        return 1
    if abs(total_rise - floor_height * span) > max(300.0, floor_height * 0.12):
        return 1
    return span if len(ordered_numbered_levels(tables)) >= span + 1 else 1


def common_level_floor_height(tables: dict[str, list[dict[str, Any]]]) -> float | None:
    heights = [number(row.get("Floor_Height")) for row in tables.get("Levels.csv", [])]
    values = [height for height in heights if height and 1800 <= height <= 6000]
    if values:
        return values[0]
    elevations = [number(row.get("Elevation")) for row in ordered_numbered_levels(tables)]
    deltas = [b - a for a, b in zip(elevations, elevations[1:]) if a is not None and b is not None and b > a]
    deltas = [delta for delta in deltas if 1800 <= delta <= 6000]
    return deltas[0] if deltas else None


def ordered_numbered_levels(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = [row for row in tables.get("Levels.csv", []) if number(row.get("Level_Number")) is not None]
    return sorted(rows, key=lambda row: number(row.get("Level_Number")) or 9999)


def append_stairwell_openings(
    project_id: str,
    tables: dict[str, list[dict[str, Any]]],
    counters: dict[str, int],
    pending: list[dict[str, Any]],
) -> None:
    for item in pending:
        boundary_pairs = parse_point_pairs(item.get("boundary"))
        if len(boundary_pairs) < 3:
            continue
        host_floors = target_floors_for_stair_opening(tables, item)
        if not host_floors:
            append_stairwell_opening_row(project_id, tables, counters, item, boundary_pairs, None, projected=False)
            continue
        projected = not bool(item.get("host_floor_id"))
        for floor in host_floors:
            append_stairwell_opening_row(project_id, tables, counters, item, boundary_pairs, floor, projected=projected)


def target_floors_for_stair_opening(tables: dict[str, list[dict[str, Any]]], item: dict[str, Any]) -> list[dict[str, Any]]:
    host_floor_id = str(item.get("host_floor_id") or "")
    if host_floor_id:
        return [row for row in tables.get("Floors.csv", []) if row.get("Element_ID") == host_floor_id]

    start_number = level_number_from_label(item.get("start_level"))
    end_number = level_number_from_label(item.get("end_level"))
    span_count = int(number(item.get("level_span_count")) or 0)
    floor_rows = sorted(
        tables.get("Floors.csv", []),
        key=lambda row: floor_level_number(tables, row) if floor_level_number(tables, row) is not None else 9999,
    )
    if start_number is not None and end_number is not None and end_number > start_number:
        target_numbers = set(range(start_number + 1, end_number + 1))
        return [row for row in floor_rows if floor_level_number(tables, row) in target_numbers]

    if span_count > 1 and floor_rows:
        base_number = floor_level_number(tables, floor_rows[0])
        if base_number is None:
            return floor_rows[1 : span_count + 1]
        target_numbers = set(range(base_number + 1, base_number + span_count + 1))
        return [row for row in floor_rows if floor_level_number(tables, row) in target_numbers]

    return []


def append_stairwell_opening_row(
    project_id: str,
    tables: dict[str, list[dict[str, Any]]],
    counters: dict[str, int],
    item: dict[str, Any],
    boundary_pairs: list[tuple[float, float]],
    host_floor: dict[str, Any] | None,
    projected: bool,
) -> None:
    counters["FLOOROPENING"] += 1
    opening_id = f"FLOOROPENING-{counters['FLOOROPENING']:03d}"
    min_x, min_y, max_x, max_y = bbox_from_pairs(boundary_pairs)
    confidence = item.get("confidence") or 0
    host_floor_id = str(host_floor.get("Element_ID") or "") if host_floor else ""
    drawing_id = host_floor.get("Drawing_ID") if host_floor else item.get("drawing_id", "")
    level_id = host_floor.get("Level_ID") if host_floor else item.get("level_id", "")
    source = "stair_boundary_projected_to_host_floor" if projected else "stair_boundary"
    needs_review = projected or not host_floor_id or confidence < REVIEW_THRESHOLD
    remarks = f"Stairwell opening inferred from {item.get('stair_id')} boundary."
    if projected:
        remarks += " Boundary came from stair detail and should be spatially mapped to the host floor by the space agent."
    elif not host_floor_id:
        remarks += " host floor needs mapping."
    tables["Floor_Openings.csv"].append(
        {
            "Opening_ID": opening_id,
            "Element_Name": f"Stairwell Opening {counters['FLOOROPENING']:03d}",
            "Project_ID": project_id,
            "Drawing_ID": drawing_id,
            "Level_ID": level_id,
            "Host_Floor_ID": host_floor_id,
            "Opening_Type": "stairwell_opening",
            "Boundary_Points": format_points(boundary_pairs),
            "Center_X": round((min_x + max_x) / 2, 3),
            "Center_Y": round((min_y + max_y) / 2, 3),
            "Center_Z": 0,
            "Width": round(max_x - min_x, 3),
            "Depth": round(max_y - min_y, 3),
            "Area": round(abs(polygon_area_from_pairs(boundary_pairs)), 3),
            "Source": source,
            "Confidence": confidence,
            "Source_Geometry_Count": item.get("source_geometry_count", ""),
            "Needs_Review": bool_text(needs_review),
            "Remarks": remarks,
        }
    )
    if host_floor:
        ids = [part for part in str(host_floor.get("Opening_IDs") or "").split(";") if part]
        if opening_id not in ids:
            ids.append(opening_id)
        host_floor["Opening_IDs"] = ";".join(ids)
        host_floor["Opening_Count"] = len(ids)


def floor_level_number(tables: dict[str, list[dict[str, Any]]], floor: dict[str, Any]) -> int | None:
    level_id = floor.get("Level_ID")
    level = next((row for row in tables.get("Levels.csv", []) if row.get("Level_ID") == level_id), None)
    value = number(level.get("Level_Number")) if level else None
    return int(value) if value is not None else None


def level_number_from_label(value: object) -> int | None:
    text = str(value or "")
    if not text.strip():
        return None
    match = re.search(r"([一二三四五六七八九十\d]+)\s*层|L\s*(\d+)|F\s*(\d+)", text, re.I)
    if not match:
        return None
    raw = match.group(1) or match.group(2) or match.group(3)
    return unicode_chinese_number(raw)


def unicode_chinese_number(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + digits.get(text[1:], 0)
    if "十" in text:
        left, right = text.split("十", 1)
        return digits.get(left, 0) * 10 + digits.get(right, 0)
    return digits.get(text)


def build_manifest(tables: dict[str, list[dict[str, Any]]], now: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for file_name in CSV_SCHEMAS:
        if file_name == "Manifest.csv":
            continue
        count = len(tables.get(file_name, []))
        rows.append(
            {
                "File_Name": file_name,
                "Data_Type": file_name.removesuffix(".csv"),
                "Record_Count": count,
                "Schema_Version": SCHEMA_VERSION,
                "Generated_Time": now.isoformat(timespec="seconds"),
                "Status": "success" if count else "empty",
                "Remarks": "" if count else "No records exported",
            }
        )
    return rows


def uncertain_rows(tables: dict[str, list[dict[str, Any]]], validation_errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    drawing_types = {
        str(row.get("Drawing_ID", "")): str(row.get("Drawing_Type", "")).strip().lower()
        for row in tables.get("Drawings.csv", [])
    }
    for file_name, element_type in [("Walls.csv", "Wall"), ("Doors.csv", "Door"), ("Windows.csv", "Window")]:
        for row in tables.get(file_name, []):
            if row.get("Needs_Review") != "true":
                continue
            drawing_type = drawing_types.get(str(row.get("Drawing_ID", "")), "")
            if element_type in {"Door", "Window"} and drawing_type and drawing_type != "floor_plan":
                continue
            if (
                element_type == "Wall"
                and str(row.get("Remarks", "")).strip() == "墙高未从图纸直接识别，使用默认墙高。"
                and (number(row.get("Confidence")) or 0) >= REVIEW_THRESHOLD
            ):
                continue
            element_id = row.get("Element_ID")
            if not element_id or element_id in seen:
                continue
            seen.add(str(element_id))
            rows.append(
                {
                    "Element_ID": element_id,
                    "Element_Type": element_type,
                    "Project_ID": row.get("Project_ID", ""),
                    "Drawing_ID": row.get("Drawing_ID", ""),
                    "Reason": row.get("Remarks", "") or "识别置信度低于阈值或关键字段缺失。",
                    "Confidence": row.get("Confidence", ""),
                    "Recommended_Action": "人工复核该构件。",
                }
            )
    for error in validation_errors:
        record_id = str(error.get("Record_ID", ""))
        if not record_id or record_id in seen:
            continue
        seen.add(record_id)
        rows.append(
            {
                "Element_ID": record_id,
                "Element_Type": error.get("File_Name", "").removesuffix(".csv"),
                "Project_ID": "PROJECT-001",
                "Drawing_ID": "",
                "Reason": error.get("Error_Message", ""),
                "Confidence": "",
                "Recommended_Action": error.get("Recommended_Action", "人工复核。"),
            }
        )
    return rows


def write_fixed_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: csv_value(row.get(header, "")) for header in headers})


def append_opening_wall_run_map(
    tables: dict[str, list[dict[str, Any]]],
    project_id: str,
    drawing_id: str,
    opening_id: str,
    opening_type: str,
    raw_host_run: str,
    host_run: str,
    host: str,
    center: tuple[float, float] | None,
    result: dict,
    confidence: float,
) -> None:
    if not host_run:
        return
    distance_value = opening_distance_from_wall_run_start(center, raw_host_run, result)
    tables["Opening_Wall_Run_Map.csv"].append(
        {
            "Opening_ID": opening_id,
            "Opening_Type": opening_type,
            "Project_ID": project_id,
            "Drawing_ID": drawing_id,
            "Wall_Run_ID": host_run,
            "Host_Wall_ID": host,
            "Relationship_Type": "hosted_by_logical_wall_run",
            "Distance_From_Run_Start": distance_value,
            "Confidence": confidence,
            "Remarks": "Revit建模时优先使用 Wall_Run_ID 作为门窗宿主墙。",
        }
    )


def opening_distance_from_wall_run_start(center: tuple[float, float] | None, raw_host_run: str, result: dict) -> Any:
    if center is None:
        return ""
    run = next((item for item in result.get("wall_runs", []) if str(item.get("id")) == raw_host_run), None)
    if not run:
        return ""
    start = point(run.get("local_start") or run.get("start"))
    end = point(run.get("local_end") or run.get("end"))
    if start is None or end is None:
        return ""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = hypot(dx, dy)
    if length <= 0:
        return ""
    distance_along = ((center[0] - start[0]) * dx + (center[1] - start[1]) * dy) / length
    return round(max(0.0, min(length, distance_along)), 3)


def list_value(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value in {None, ""}:
        return []
    return [value]


def split_semicolon(value: object) -> list[str]:
    if value in {None, ""}:
        return []
    return [part for part in str(value).split(";") if part]


def write_ai_model_files(
    model_path: Path,
    elements_path: Path,
    readme_path: Path,
    project_id: str,
    project_name: str,
    tables: dict[str, list[dict[str, Any]]],
    validation_errors: list[dict[str, Any]],
    now: datetime,
) -> None:
    model_path.write_text(
        json.dumps(
            build_ai_model(project_id, project_name, tables, validation_errors, now),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    with elements_path.open("w", encoding="utf-8", newline="\n") as f:
        for record in build_ai_element_records(tables):
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    readme_path.write_text(ai_readme_text(), encoding="utf-8")


def build_ai_model(
    project_id: str,
    project_name: str,
    tables: dict[str, list[dict[str, Any]]],
    validation_errors: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    drawings = []
    for drawing in tables.get("Drawings.csv", []):
        drawing_id = str(drawing.get("Drawing_ID", ""))
        drawings.append(
            {
                **normalize_row(drawing),
                "elements": {
                    "grids": normalize_rows(rows_for_drawing(tables, "Grids.csv", drawing_id)),
                    "walls": normalize_rows(rows_for_drawing(tables, "Walls.csv", drawing_id)),
                    "wall_runs": normalize_rows(rows_for_drawing(tables, "Wall_Runs.csv", drawing_id)),
                    "columns": normalize_rows(rows_for_drawing(tables, "Columns.csv", drawing_id)),
                    "floors": normalize_rows(rows_for_drawing(tables, "Floors.csv", drawing_id)),
                    "roofs": normalize_rows(rows_for_drawing(tables, "Roofs.csv", drawing_id)),
                    "parapets": normalize_rows(rows_for_drawing(tables, "Parapets.csv", drawing_id)),
                    "floor_openings": normalize_rows(rows_for_drawing(tables, "Floor_Openings.csv", drawing_id)),
                    "stairs": normalize_rows(rows_for_drawing(tables, "Stairs.csv", drawing_id)),
                    "railings": normalize_rows(rows_for_drawing(tables, "Railings.csv", drawing_id)),
                    "doors": normalize_rows(rows_for_drawing(tables, "Doors.csv", drawing_id)),
                    "windows": normalize_rows(rows_for_drawing(tables, "Windows.csv", drawing_id)),
                },
                "opening_wall_run_links": normalize_rows(rows_for_drawing(tables, "Opening_Wall_Run_Map.csv", drawing_id)),
                "annotations": {
                    "dimensions": normalize_rows(rows_for_drawing(tables, "Dimensions.csv", drawing_id)),
                    "texts": normalize_rows(rows_for_drawing(tables, "Text_Annotations.csv", drawing_id)),
                },
                "review_items": normalize_rows(rows_for_drawing(tables, "Uncertain_Elements.csv", drawing_id)),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "exporter_version": EXPORTER_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "unit": UNIT,
        "coordinate_system": "local",
        "project": normalize_row(tables.get("Project_Info.csv", [{}])[0]) or {
            "project_id": project_id,
            "project_name": project_name,
        },
        "summary": {
            "drawings": len(tables.get("Drawings.csv", [])),
            "levels": len(tables.get("Levels.csv", [])),
            "grids": len(tables.get("Grids.csv", [])),
            "walls": len(tables.get("Walls.csv", [])),
            "wall_runs": len(tables.get("Wall_Runs.csv", [])),
            "columns": len(tables.get("Columns.csv", [])),
            "floors": len(tables.get("Floors.csv", [])),
            "roofs": len(tables.get("Roofs.csv", [])),
            "parapets": len(tables.get("Parapets.csv", [])),
            "floor_openings": len(tables.get("Floor_Openings.csv", [])),
            "stairs": len(tables.get("Stairs.csv", [])),
            "railings": len(tables.get("Railings.csv", [])),
            "doors": len(tables.get("Doors.csv", [])),
            "windows": len(tables.get("Windows.csv", [])),
            "dimensions": len(tables.get("Dimensions.csv", [])),
            "text_annotations": len(tables.get("Text_Annotations.csv", [])),
            "materials": len(tables.get("Materials.csv", [])),
            "material_links": len(tables.get("Element_Material_Map.csv", [])),
            "review_items": len(tables.get("Uncertain_Elements.csv", [])),
            "validation_errors": len(validation_errors),
        },
        "levels": normalize_rows(tables.get("Levels.csv", [])),
        "material_catalog": normalize_rows(tables.get("Materials.csv", [])),
        "material_links": normalize_rows(tables.get("Element_Material_Map.csv", [])),
        "material_linking": {
            "status": "reserved",
            "description": "Materials are not inferred from geometry. Future AI or Revit steps can populate Materials.csv and Element_Material_Map.csv, then update each element material slot.",
            "recommended_sources": ["associated_text_annotations", "drawing_material_notes", "finish_schedule", "door_window_schedule", "external_material_database", "manual_review"],
        },
        "semantic_context": build_semantic_context(tables),
        "drawings": drawings,
        "validation_errors": normalize_rows(validation_errors),
        "file_index": {
            "ai_elements_jsonl": AI_ELEMENTS_NAME,
            "csv_manifest": f"{CSV_TABLE_DIR_NAME}/Manifest.csv",
            "csv_tables_dir": CSV_TABLE_DIR_NAME,
            "materials_csv": f"{CSV_TABLE_DIR_NAME}/Materials.csv",
            "element_material_map_csv": f"{CSV_TABLE_DIR_NAME}/Element_Material_Map.csv",
        },
    }


def build_ai_element_records(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    drawing_names = {row.get("Drawing_ID"): row.get("Drawing_Name", "") for row in tables.get("Drawings.csv", [])}
    records: list[dict[str, Any]] = []
    for row in tables.get("Grids.csv", []):
        records.append(ai_line_record("grid", row.get("Grid_ID"), row, drawing_names, "Start", "End"))
    for row in tables.get("Walls.csv", []):
        record = ai_line_record("wall", row.get("Element_ID"), row, drawing_names, "Start", "End")
        record["dimensions"] = compact_dict(
            {
                "length": smart_value(row.get("Length")),
                "thickness": smart_value(row.get("Thickness")),
                "height": smart_value(row.get("Height")),
            }
        )
        record["classification"] = smart_value(row.get("Wall_Type"))
        record["material"] = material_placeholder(row, "wall")
        records.append(record)
    for row in tables.get("Wall_Runs.csv", []):
        record = ai_line_record("wall_run", row.get("Wall_Run_ID"), row, drawing_names, "Start", "End")
        record["source_wall_ids"] = split_semicolon(row.get("Source_Wall_IDs"))
        record["opening_ids"] = split_semicolon(row.get("Opening_IDs"))
        record["dimensions"] = compact_dict(
            {
                "length": smart_value(row.get("Length")),
                "thickness": smart_value(row.get("Thickness")),
                "height": smart_value(row.get("Height")),
            }
        )
        record["revit_generation_hint"] = "Create this as the host wall first, then place mapped doors/windows on this wall run."
        records.append(record)
    for row in tables.get("Columns.csv", []):
        record = ai_point_element_record("column", row.get("Element_ID"), row, drawing_names)
        record["classification"] = smart_value(row.get("Column_Type"))
        record["dimensions"] = compact_dict(
            {
                "width": smart_value(row.get("Width")),
                "depth": smart_value(row.get("Depth")),
                "diameter": smart_value(row.get("Diameter")),
                "height": smart_value(row.get("Height")),
            }
        )
        record["material"] = material_placeholder(row, "column")
        records.append(record)
    for row in tables.get("Floors.csv", []):
        record = ai_polygon_element_record("floor", row.get("Element_ID"), row, drawing_names, "Boundary_Points")
        record["classification"] = smart_value(row.get("Floor_Type"))
        record["dimensions"] = compact_dict(
            {
                "area": smart_value(row.get("Area")),
                "thickness": smart_value(row.get("Thickness")),
                "elevation": smart_value(row.get("Elevation")),
            }
        )
        record["opening_ids"] = split_semicolon(row.get("Opening_IDs"))
        record["material"] = material_placeholder(row, "floor")
        record["revit_generation_hint"] = "Create this floor slab first, then cut mapped floor_openings from its boundary."
        records.append(record)
    for row in tables.get("Roofs.csv", []):
        record = ai_polygon_element_record("roof", row.get("Element_ID"), row, drawing_names, "Boundary_Points")
        record["classification"] = smart_value(row.get("Roof_Type"))
        record["dimensions"] = compact_dict(
            {
                "area": smart_value(row.get("Area")),
                "thickness": smart_value(row.get("Thickness")),
                "elevation": smart_value(row.get("Elevation")),
                "slope": smart_value(row.get("Slope")),
            }
        )
        record["material"] = material_placeholder(row, "roof")
        record["revit_generation_hint"] = "Create a flat roof using this boundary; review material, drainage and slope before production modeling."
        records.append(record)
    for row in tables.get("Parapets.csv", []):
        record = ai_line_record("parapet", row.get("Element_ID"), row, drawing_names, "Start", "End")
        record["host_roof_id"] = smart_value(row.get("Host_Roof_ID"))
        record["classification"] = smart_value(row.get("Parapet_Type"))
        record["dimensions"] = compact_dict(
            {
                "length": smart_value(row.get("Length")),
                "thickness": smart_value(row.get("Thickness")),
                "height": smart_value(row.get("Height")),
            }
        )
        record["dimension_sources"] = compact_dict({"height": smart_value(row.get("Height_Source"))})
        record["material"] = material_placeholder(row, "parapet")
        record["revit_generation_hint"] = "Create this parapet as a wall along the roof boundary; default height/thickness require review."
        records.append(record)
    for row in tables.get("Floor_Openings.csv", []):
        record = ai_polygon_element_record("floor_opening", row.get("Opening_ID"), row, drawing_names, "Boundary_Points")
        record["host_floor_id"] = smart_value(row.get("Host_Floor_ID"))
        record["classification"] = smart_value(row.get("Opening_Type"))
        record["geometry"]["center"] = point3(row, "Center")
        record["dimensions"] = compact_dict(
            {
                "width": smart_value(row.get("Width")),
                "depth": smart_value(row.get("Depth")),
                "area": smart_value(row.get("Area")),
            }
        )
        record["revit_generation_hint"] = "Cut this opening from Host_Floor_ID using the polygon boundary."
        records.append(record)
    for row in tables.get("Stairs.csv", []):
        record = ai_line_record("stair", row.get("Element_ID"), row, drawing_names, "Start", "End")
        record["classification"] = smart_value(row.get("Stair_Type"))
        record["boundary"] = parse_points(row.get("Boundary_Points"))
        record["stairwell_opening"] = compact_dict(
            {
                "opening_required": smart_value(row.get("Opening_Required")),
                "opening_id": smart_value(row.get("Stairwell_Opening_ID")),
                "boundary": parse_points(row.get("Stairwell_Opening_Boundary")),
            }
        )
        record["levels"] = compact_dict(
            {
                "start_level": smart_value(row.get("Start_Level_ID")),
                "end_level": smart_value(row.get("End_Level_ID")),
                "level_span_count": smart_value(row.get("Level_Span_Count")),
            }
        )
        record["stair_core_id"] = smart_value(row.get("Stair_Core_ID"))
        record["stair_segment_id"] = smart_value(row.get("Stair_Segment_ID"))
        record["stair_segment_number"] = smart_value(row.get("Stair_Segment_Number"))
        record["dimensions"] = compact_dict(
            {
                "total_rise": smart_value(row.get("Total_Rise")),
                "total_run": smart_value(row.get("Total_Run")),
                "width": smart_value(row.get("Width")),
                "stairwell_width": smart_value(row.get("Stairwell_Width")),
                "run_count": smart_value(row.get("Run_Count")),
                "risers_per_run": smart_value(row.get("Risers_Per_Run")),
                "treads_per_run": smart_value(row.get("Treads_Per_Run")),
                "run_length": smart_value(row.get("Run_Length")),
                "landing_length": smart_value(row.get("Landing_Length")),
                "landing_width": smart_value(row.get("Landing_Width")),
                "riser_height": smart_value(row.get("Riser_Height")),
                "tread_depth": smart_value(row.get("Tread_Depth")),
                "number_of_risers": smart_value(row.get("Number_Of_Risers")),
                "number_of_treads": smart_value(row.get("Number_Of_Treads")),
            }
        )
        record["direction"] = smart_value(row.get("Direction"))
        record["revit_generation_hint"] = "Cut the stairwell_opening boundary from the host floor, then create a parallel double-run stair using run/landing parameters; review landing dimensions before production modeling."
        records.append(record)
    for row in tables.get("Railings.csv", []):
        record = ai_line_record("railing", row.get("Element_ID"), row, drawing_names, "Start", "End")
        record["classification"] = smart_value(row.get("Railing_Type"))
        record["related_stair_id"] = smart_value(row.get("Related_Stair_ID"))
        record["dimensions"] = compact_dict(
            {
                "height": smart_value(row.get("Height")),
                "distance_to_stairwell": smart_value(row.get("Distance_To_Stairwell")),
            }
        )
        record["revit_generation_hint"] = "Create this railing along the centerline; use Distance_To_Stairwell to place it relative to the stairwell opening."
        records.append(record)
    for row in tables.get("Doors.csv", []):
        record = ai_opening_record("door", row.get("Element_ID"), row, drawing_names, "Door_Type")
        record["mark"] = smart_value(row.get("Door_Mark"))
        record["dimension_sources"] = compact_dict(
            {
                "width": smart_value(row.get("Width_Source")),
                "height": smart_value(row.get("Height_Source")),
            }
        )
        record["cross_view_reference"] = compact_dict(
            {
                "matched_elevation_drawing": smart_value(row.get("Matched_Elevation_Drawing")),
                "matched_elevation_door_id": smart_value(row.get("Matched_Elevation_Door_ID")),
                "match_score": smart_value(row.get("Cross_View_Match_Score")),
                "match_status": smart_value(row.get("Cross_View_Match_Status")),
            }
        )
        records.append(compact_dict(record))
    for row in tables.get("Windows.csv", []):
        record = ai_opening_record("window", row.get("Element_ID"), row, drawing_names, "Window_Type")
        record["dimensions"]["sill_height"] = smart_value(row.get("Sill_Height"))
        record["dimension_sources"] = compact_dict(
            {
                "width": smart_value(row.get("Width_Source")),
                "height": smart_value(row.get("Height_Source")),
                "sill_height": smart_value(row.get("Sill_Height_Source")),
            }
        )
        record["cross_view_reference"] = compact_dict(
            {
                "matched_elevation_drawing": smart_value(row.get("Matched_Elevation_Drawing")),
                "matched_elevation_window_id": smart_value(row.get("Matched_Elevation_Window_ID")),
                "match_score": smart_value(row.get("Cross_View_Match_Score")),
            }
        )
        record["dimensions"] = compact_dict(record["dimensions"])
        record = compact_dict(record)
        records.append(record)
    for row in tables.get("Dimensions.csv", []):
        records.append(ai_annotation_record("dimension", row.get("Dimension_ID"), row, drawing_names))
    for row in tables.get("Text_Annotations.csv", []):
        records.append(ai_annotation_record("text", row.get("Text_ID"), row, drawing_names))
    return records


def build_semantic_context(tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    associations = associated_text_rows(tables)
    return {
        "text_element_associations": normalize_rows(associations),
        "space_semantic_inputs": {
            "purpose": "Use nearby CAD text to infer room names, space functions, and element ownership without changing base geometry.",
            "candidate_texts": normalize_rows(space_semantic_text_rows(tables)),
            "associated_texts": normalize_rows(
                [
                    row
                    for row in associations
                    if row.get("Associated_Element_Type") in {"wall", "wall_run", "door", "window", "floor", "stair", "floor_opening"}
                ]
            ),
        },
        "material_semantic_inputs": {
            "purpose": "Use associated CAD text as evidence for material, finish, fire rating, waterproofing, thickness, and construction practice agents.",
            "candidate_texts": normalize_rows(material_semantic_text_rows(tables)),
            "associated_texts": normalize_rows(material_associated_text_rows(associations)),
        },
    }


def associated_text_rows(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [row for row in tables.get("Text_Annotations.csv", []) if row.get("Associated_Element_ID")]


def space_semantic_text_rows(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for row in tables.get("Text_Annotations.csv", []):
        text = str(row.get("Text_Content") or "")
        if text and not is_dimension_like_text(text):
            rows.append(row)
    return rows


def material_semantic_text_rows(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [row for row in tables.get("Text_Annotations.csv", []) if is_material_semantic_text(row.get("Text_Content"))]


def material_associated_text_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    material_types = {"wall", "wall_run", "column", "floor", "roof", "parapet", "door", "window", "stair", "railing"}
    return [
        row
        for row in rows
        if row.get("Associated_Element_Type") in material_types or is_material_semantic_text(row.get("Text_Content"))
    ]


def is_dimension_like_text(value: object) -> bool:
    text = single_line(value)
    if not text:
        return True
    return bool(re.fullmatch(r"[0-9.,+\-*/ xX×~～=()（）]+(?:mm|m|米)?", text, re.I))


def is_material_semantic_text(value: object) -> bool:
    text = single_line(value).lower()
    if not text:
        return False
    keywords = [
        "material",
        "finish",
        "fire",
        "waterproof",
        "concrete",
        "steel",
        "brick",
        "mortar",
        "insulation",
        "材料",
        "做法",
        "面层",
        "基层",
        "防水",
        "保温",
        "混凝土",
        "砌块",
        "砖",
        "砂浆",
        "钢筋",
        "耐火",
        "厚",
    ]
    return any(keyword in text for keyword in keywords)


def ai_line_record(
    element_type: str,
    element_id: object,
    row: dict[str, Any],
    drawing_names: dict[object, object],
    start_prefix: str,
    end_prefix: str,
) -> dict[str, Any]:
    drawing_id = row.get("Drawing_ID", "")
    return compact_dict(
        {
            "record_type": "element",
            "element_type": element_type,
            "element_id": element_id,
            "drawing_id": drawing_id,
            "drawing_name": drawing_names.get(drawing_id, ""),
            "level_id": row.get("Level_ID", ""),
            "geometry": {
                "type": "line",
                "start": point3(row, start_prefix),
                "end": point3(row, end_prefix),
                "angle": smart_value(row.get("Angle") or row.get("Rotation_Angle")),
            },
            "confidence": smart_value(row.get("Confidence")),
            "needs_review": smart_value(row.get("Needs_Review")),
            "remarks": smart_value(row.get("Remarks")),
            "properties": normalize_row(row),
        }
    )


def ai_point_element_record(
    element_type: str,
    element_id: object,
    row: dict[str, Any],
    drawing_names: dict[object, object],
) -> dict[str, Any]:
    drawing_id = row.get("Drawing_ID", "")
    return compact_dict(
        {
            "record_type": "element",
            "element_type": element_type,
            "element_id": element_id,
            "drawing_id": drawing_id,
            "drawing_name": drawing_names.get(drawing_id, ""),
            "level_id": row.get("Level_ID", ""),
            "geometry": {
                "type": "point",
                "center": point3(row, "Center"),
                "rotation_angle": smart_value(row.get("Rotation_Angle")),
            },
            "confidence": smart_value(row.get("Confidence")),
            "needs_review": smart_value(row.get("Needs_Review")),
            "remarks": smart_value(row.get("Remarks")),
            "properties": normalize_row(row),
        }
    )


def ai_polygon_element_record(
    element_type: str,
    element_id: object,
    row: dict[str, Any],
    drawing_names: dict[object, object],
    boundary_field: str,
) -> dict[str, Any]:
    drawing_id = row.get("Drawing_ID", "")
    return compact_dict(
        {
            "record_type": "element",
            "element_type": element_type,
            "element_id": element_id,
            "drawing_id": drawing_id,
            "drawing_name": drawing_names.get(drawing_id, ""),
            "level_id": row.get("Level_ID", ""),
            "geometry": {
                "type": "polygon",
                "boundary": parse_points(row.get(boundary_field)),
            },
            "source": smart_value(row.get("Source")),
            "confidence": smart_value(row.get("Confidence")),
            "needs_review": smart_value(row.get("Needs_Review")),
            "remarks": smart_value(row.get("Remarks")),
            "properties": normalize_row(row),
        }
    )


def ai_opening_record(
    element_type: str,
    element_id: object,
    row: dict[str, Any],
    drawing_names: dict[object, object],
    type_field: str,
) -> dict[str, Any]:
    drawing_id = row.get("Drawing_ID", "")
    record = compact_dict(
        {
            "record_type": "element",
            "element_type": element_type,
            "element_id": element_id,
            "drawing_id": drawing_id,
            "drawing_name": drawing_names.get(drawing_id, ""),
            "level_id": row.get("Level_ID", ""),
            "host_wall_id": row.get("Host_Wall_ID", ""),
            "host_wall_run_id": row.get("Host_Wall_Run_ID", ""),
            "classification": smart_value(row.get("Final_Category")),
            "recognition_source": smart_value(row.get(type_field)),
            "mechanical_classification": compact_dict(
                {
                    "candidate": smart_value(row.get("Mechanical_Category")),
                    "source": smart_value(row.get("Mechanical_Category_Source")),
                    "confidence": smart_value(row.get("Mechanical_Category_Confidence")),
                    "needs_ai_classification": smart_value(row.get("Needs_AI_Classification")),
                }
            ),
            "classification_input": parse_json_object(row.get("Classification_Input")),
            "geometry": {"type": "point", "center": point3(row, "Center")},
            "dimensions": compact_dict(
                {
                    "width": smart_value(row.get("Width")),
                    "width_source": smart_value(row.get("Width_Source")),
                    "height": smart_value(row.get("Height")),
                    "thickness": smart_value(row.get("Thickness")),
                }
            ),
            "material": material_placeholder(row, element_type),
            "confidence": smart_value(row.get("Confidence")),
            "needs_review": smart_value(row.get("Needs_Review")),
            "remarks": smart_value(row.get("Remarks")),
            "properties": normalize_row(row),
        }
    )
    if element_type == "door":
        record["opening_direction"] = smart_value(row.get("Opening_Direction"))
        record["swing_side"] = smart_value(row.get("Swing_Side"))
        record["swing_angle"] = smart_value(row.get("Swing_Angle"))
        record["swing_source"] = smart_value(row.get("Swing_Source"))
        record["swing_confidence"] = smart_value(row.get("Swing_Confidence"))
        record["panel_start"] = point3(row, "Panel_Start")
        record["panel_end"] = point3(row, "Panel_End")
        record["panel_thickness_mm"] = smart_value(row.get("Panel_Thickness"))
        record["panel_wall_angle_deg"] = smart_value(row.get("Panel_Wall_Angle"))
        record = compact_dict(record)
    return record


def material_placeholder(row: dict[str, Any], element_type: str) -> dict[str, Any]:
    name = smart_value(row.get("Material") or row.get("Material_Name"))
    material_id = smart_value(row.get("Material_ID"))
    return compact_dict(
        {
            "status": "assigned" if material_id or name else "unassigned",
            "material_id": material_id,
            "material_name": name,
            "material_role": "primary",
            "element_type": element_type,
            "source": "not_recognized" if not material_id and not name else smart_value(row.get("Material_Source")) or "existing_field",
            "confidence": smart_value(row.get("Material_Confidence")),
            "needs_review": True if not material_id and not name else smart_value(row.get("Material_Needs_Review")),
        }
    )


def ai_annotation_record(
    annotation_type: str,
    annotation_id: object,
    row: dict[str, Any],
    drawing_names: dict[object, object],
) -> dict[str, Any]:
    drawing_id = row.get("Drawing_ID", "")
    content = row.get("Text_Content") if annotation_type == "text" else row.get("Value")
    point_prefix = "Local" if annotation_type == "text" else "Start"
    return compact_dict(
        {
            "record_type": "annotation",
            "annotation_type": annotation_type,
            "annotation_id": annotation_id,
            "drawing_id": drawing_id,
            "drawing_name": drawing_names.get(drawing_id, ""),
            "geometry": {"type": "point", "point": point3(row, point_prefix)},
            "content": smart_value(content),
            "associated_element": compact_dict(
                {
                    "element_id": smart_value(row.get("Associated_Element_ID")),
                    "element_type": smart_value(row.get("Associated_Element_Type")),
                    "distance": smart_value(row.get("Association_Distance")),
                    "method": smart_value(row.get("Association_Method")),
                }
            ),
            "confidence": smart_value(row.get("Confidence")),
            "needs_review": smart_value(row.get("Needs_Review")),
            "properties": normalize_row(row),
        }
    )


def rows_for_drawing(tables: dict[str, list[dict[str, Any]]], file_name: str, drawing_id: str) -> list[dict[str, Any]]:
    return [row for row in tables.get(file_name, []) if str(row.get("Drawing_ID", "")) == drawing_id]


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_row(row) for row in rows]


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in row.items():
        converted = smart_value(value, key)
        if is_empty_ai_value(converted):
            continue
        normalized[snake_key(key)] = converted
    return normalized


def snake_key(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def smart_value(value: object, key: str = "") -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text:
        return None
    lower = text.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if key.endswith("_ID") or key in {"Element_ID", "Drawing_ID", "Project_ID", "Level_ID"}:
        return text
    try:
        parsed = float(text)
    except ValueError:
        return text
    if parsed.is_integer():
        return int(parsed)
    return parsed


def point3(row: dict[str, Any], prefix: str) -> dict[str, Any]:
    return compact_dict(
        {
            "x": smart_value(row.get(f"{prefix}_X")),
            "y": smart_value(row.get(f"{prefix}_Y")),
            "z": smart_value(row.get(f"{prefix}_Z")),
        }
    )


def compact_dict(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        cleaned = compact_value(item)
        if not is_empty_ai_value(cleaned):
            result[key] = cleaned
    return result


def compact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return compact_dict(value)
    if isinstance(value, list):
        return [item for item in (compact_value(item) for item in value) if not is_empty_ai_value(item)]
    return value


def is_empty_ai_value(value: Any) -> bool:
    return value is None or value == "" or value == {} or value == []


def ai_readme_text() -> str:
    return "\n".join(
        [
            "# AI model data",
            "",
            "This folder contains AI-friendly structured model data.",
            "",
            "- `AI_Model.json`: complete nested project model grouped by drawing.",
            "- `AI_Elements.jsonl`: one recognized element or annotation per line, suitable for streaming or vector indexing.",
            f"- `{CSV_TABLE_DIR_NAME}/`: normalized CSV tables kept for Revit, spreadsheets, and compatibility.",
            "",
            "Material information is reserved but not fabricated. Use `material_catalog`, `material_links`, `Materials.csv`, and `Element_Material_Map.csv` to connect future AI-extracted or manually reviewed material data to recognized elements.",
            "Text annotations include nearest-element association fields. Space and material agents should use `semantic_context`, `Associated_Element_ID`, `Associated_Element_Type`, and `Association_Distance` as evidence for semantic completion.",
            "Door/window category fields are intentionally split into blank final categories and rule-based mechanical candidates. Use `classification_input`, nearby raw geometry, block names, annotations, and cross-view references for later AI classification.",
            "",
            "Coordinates and sizes use millimeters in a local drawing coordinate system.",
            "",
        ]
    )


def append_element_summary(lines: list[str], title: str, rows: list[dict[str, Any]], id_field: str, fields: list[str]) -> None:
    lines.extend([f"## {title}", ""])
    if not rows:
        lines.extend(["- 暂无识别结果。", ""])
        return
    for row in rows:
        parts = [f"{field}={row.get(field, '')}" for field in fields]
        lines.append(f"- {row.get(id_field, '')}：{'，'.join(parts)}")
    lines.append("")


def error_row(index: int, severity: str, file_name: str, record_id: object, field_name: str, error_type: str, message: str, action: str) -> dict[str, Any]:
    return {
        "Error_ID": f"ERR-{index:04d}",
        "Severity": severity,
        "File_Name": file_name,
        "Record_ID": record_id,
        "Field_Name": field_name,
        "Error_Type": error_type,
        "Error_Message": message,
        "Recommended_Action": action,
    }


def normalize_drawing_type(value: object) -> str:
    mapping = {
        "architectural_plan": "floor_plan",
        "architectural_elevation": "elevation",
        "architectural_section": "section",
        "architectural_detail": "detail",
        "general_assembly": "site_plan",
    }
    return mapping.get(str(value), "unknown")


def infer_project_levels(
    project_id: str,
    drawing_results: list[tuple[str, dict]],
    default_floor_height: float,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    inferred: dict[str, dict[str, Any]] = {}
    drawing_keys: dict[str, str] = {}
    level_titles = [level_search_text(drawing_name, result) for drawing_name, result in drawing_results]
    uses_british_floor_names = any(
        re.search(r"\bground\s+floor\b", normalize_level_text(title), re.I)
        for title in level_titles
    )
    for (drawing_name, result), title in zip(drawing_results, level_titles):
        info = infer_level_from_title(title, british_storey_numbering=uses_british_floor_names)
        if info is None:
            drawing_keys[drawing_name] = ""
            continue
        key = str(info["key"])
        drawing_keys[drawing_name] = key
        existing = inferred.get(key)
        if existing is None or float(info.get("confidence", 0)) > float(existing.get("confidence", 0)):
            inferred[key] = info

    max_floor = max((int(item["number"]) for item in inferred.values() if isinstance(item.get("number"), int)), default=1)
    for item in inferred.values():
        if item.get("key") == "roof":
            item["number"] = max_floor + 1

    ordered = sorted(inferred.values(), key=level_sort_key)
    key_to_id: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(ordered, start=1):
        level_id = f"LEVEL-{index:03d}"
        key_to_id[str(item["key"])] = level_id
        number_value = item.get("number")
        rows.append(
            {
                "Level_ID": level_id,
                "Project_ID": project_id,
                "Level_Name": item.get("name") or f"Level {index}",
                "Level_Number": number_value if number_value is not None else "",
                "Elevation": level_elevation(number_value, default_floor_height),
                "Floor_Height": round(default_floor_height, 3),
                "Source_Drawing_ID": item.get("source_title", ""),
                "Confidence": item.get("confidence", 0.45),
                "Needs_Review": bool_text(float(item.get("confidence", 0)) < REVIEW_THRESHOLD),
                "Remarks": item.get("remarks", ""),
            }
        )

    drawing_level_ids = {drawing_name: key_to_id.get(key, "") if key else "" for drawing_name, key in drawing_keys.items()}
    return drawing_level_ids, rows


def infer_level_from_title(title: str, *, british_storey_numbering: bool = False) -> dict[str, Any] | None:
    text = normalize_level_text(title)
    if not text:
        return None
    if re.search(r"\bground\s+floor\b", text, re.I):
        return {
            "key": "L1",
            "name": "\u4e00\u5c42",
            "number": 1,
            "confidence": 0.94,
            "source_title": title,
            "remarks": "Ground floor inferred from English drawing title.",
        }
    english_ordinal = re.search(
        r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+floor\b",
        text,
        re.I,
    )
    if english_ordinal:
        ordinal_numbers = {
            "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
            "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
        }
        ordinal = ordinal_numbers[english_ordinal.group(1).lower()]
        number = ordinal + 1 if british_storey_numbering else ordinal
        return {
            "key": f"L{number}",
            "name": f"Level {number}",
            "number": number,
            "confidence": 0.94 if british_storey_numbering else 0.78,
            "source_title": title,
            "remarks": (
                "British floor naming inferred from the presence of a Ground Floor: "
                f"{english_ordinal.group(1).title()} Floor is building storey {number}."
                if british_storey_numbering
                else "English ordinal floor inferred without a Ground Floor context; review regional convention."
            ),
        }
    if re.search(r"屋顶|屋面|roof", text, re.I):
        return {"key": "roof", "name": "屋顶层", "number": None, "confidence": 0.86, "source_title": title, "remarks": "Level inferred from drawing title."}
    basement = re.search(r"地下\s*([一二三四五六七八九十\d]+)\s*层|B\s*(\d+)", text, re.I)
    if basement:
        raw = basement.group(1) or basement.group(2)
        value = chinese_number(raw)
        if value is not None:
            number = -abs(value)
            return {"key": f"B{abs(number)}", "name": f"地下{abs(number)}层", "number": number, "confidence": 0.88, "source_title": title, "remarks": "Level inferred from drawing title."}
    if re.search(r"首层|一层|1\s*层|1F|F1", text, re.I):
        return {"key": "L1", "name": "一层", "number": 1, "confidence": 0.9, "source_title": title, "remarks": "Level inferred from drawing title."}
    floor = re.search(r"([一二三四五六七八九十\d]+)\s*层|([2-9]\d*)F|F([2-9]\d*)", text, re.I)
    if floor:
        raw = floor.group(1) or floor.group(2) or floor.group(3)
        number = chinese_number(raw)
        if number is not None:
            return {"key": f"L{number}", "name": f"{display_floor_number(number)}层", "number": number, "confidence": 0.88, "source_title": title, "remarks": "Level inferred from drawing title."}
    if re.search(r"标准层|standard\s*floor|typical\s*floor", text, re.I):
        return {"key": "typical", "name": "标准层", "number": None, "confidence": 0.76, "source_title": title, "remarks": "Typical floor inferred from drawing title; review required."}
    return None


def level_search_text(drawing_name: str, result: dict) -> str:
    notes = result.get("notes", {})
    parts = [str(notes.get("drawing_title") or "")]
    for candidate in notes.get("drawing_title_candidates", []):
        if not isinstance(candidate, dict):
            continue
        parts.append(str(candidate.get("raw_text") or ""))
        parts.append(str(candidate.get("text") or ""))
    parts.append(str(drawing_name or ""))
    return " ".join(part for part in parts if part.strip())


def display_drawing_name(drawing_name: str, result: dict) -> str:
    notes = result.get("notes", {})
    title = str(notes.get("drawing_title") or "").strip()
    for candidate in notes.get("drawing_title_candidates", []):
        if not isinstance(candidate, dict):
            continue
        raw = single_line(candidate.get("raw_text"))
        text = single_line(candidate.get("text"))
        if not raw or raw == title:
            continue
        if not text or text == title or title in raw or text in raw or infer_level_from_title(raw):
            return raw
    return title or str(drawing_name or "")


def single_line(value: object) -> str:
    return " ".join(str(value or "").split())


def normalize_level_text(value: str) -> str:
    return str(value or "").replace("－", "-").replace("_", " ")


def chinese_number(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + digits.get(text[1:], 0)
    if "十" in text:
        left, right = text.split("十", 1)
        return digits.get(left, 0) * 10 + digits.get(right, 0)
    return digits.get(text)


def display_floor_number(number: int) -> str:
    names = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
    return names.get(number, str(number))


def level_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    if item.get("key") == "default":
        return (2, 0, "default")
    number = item.get("number")
    if isinstance(number, int):
        return (0, number, str(item.get("key", "")))
    if item.get("key") == "typical":
        return (1, 0, "typical")
    return (2, 0, str(item.get("key", "")))


def level_elevation(number: object, default_floor_height: float) -> Any:
    if isinstance(number, int):
        return round((number - 1) * default_floor_height, 3)
    return ""


def first_floor_height(drawing_results: list[tuple[str, dict]]) -> float | None:
    for _name, result in drawing_results:
        heights = result.get("plan_summary", {}).get("floor_heights", [])
        for item in heights:
            value = number(item.get("height_mm"))
            if value:
                return value
    return None


def review_reason(missing_host: bool, height: object) -> str:
    parts: list[str] = []
    if missing_host:
        parts.append("未能确认所属墙体")
    if not height:
        parts.append("高度未识别")
    return "；".join(parts)


def normalize_open_direction(value: object) -> str:
    if not value:
        return "unknown"
    text = str(value).lower()
    if text in {"east", "west", "north", "south"}:
        return text
    if text in {"left", "right", "double", "sliding", "revolving"}:
        return text
    return "unknown"


def normalize_swing_side(value: object) -> str:
    if not value:
        return "unknown"
    text = str(value).lower()
    if text in {"left", "right", "double"}:
        return text
    return "unknown"


def door_category_from_source(source: object) -> str:
    text = str(source or "")
    if text == "quarter_arc":
        return "single_swing_door"
    if text == "double_swing_arc":
        return "double_swing_door"
    if text in {"sliding_door_double_rectangles", "merged_adjacent_door_frames"}:
        return "sliding_door"
    return "unknown"


def window_category_from_source(source: object) -> str:
    text = str(source or "")
    if text == "five_parallel_lines":
        return "sliding_window"
    return "unknown"


def opening_category_from_source(kind: str, source: object) -> str:
    if kind == "door":
        return door_category_from_source(source)
    if kind == "window":
        return window_category_from_source(source)
    return "unknown"


def opening_category_source(opening: dict) -> str:
    explicit = str(opening.get("component_category_source") or "").strip()
    if explicit:
        return explicit
    if opening.get("component_category"):
        return "rule_based"
    if opening.get("block_name"):
        return "block_name"
    if opening.get("source"):
        return "recognition_source"
    return "unknown"


def mechanical_category_confidence(opening: dict, category: object) -> Any:
    text = str(category or "").lower()
    if not text or text == "unknown":
        return 0
    source = str(opening.get("source") or "")
    base = number(opening.get("confidence")) or 0.0
    if source in {"quarter_arc", "double_swing_arc", "sliding_door_double_rectangles", "five_parallel_lines"}:
        return round(min(base, 0.72), 3)
    if source in {"merged_adjacent_door_frames", "elevation_rectangle"}:
        return round(min(base, 0.62), 3)
    if source == "block":
        return round(min(base, 0.5), 3)
    return round(min(base, 0.45), 3)


def opening_classification_input(
    opening: dict,
    result: dict,
    mechanical_category: object,
    category_source: str,
    host_wall_id: str,
    host_wall_run_id: str,
) -> dict[str, Any]:
    width = number(opening.get("width"))
    height = number(opening.get("height_mm") or opening.get("height"))
    sill = number(opening.get("sill_height_mm"))
    raw_items = nearby_raw_geometry(opening, result.get("raw_geometry", []))
    return compact_dict(
        {
            "opening_id": opening.get("id"),
            "kind": opening.get("kind"),
            "final_category": None,
            "mechanical_category_candidate": smart_value(mechanical_category),
            "mechanical_category_source": category_source,
            "mechanical_category_confidence": mechanical_category_confidence(opening, mechanical_category),
            "needs_ai_classification": True,
            "recognition_source": opening.get("source"),
            "source_layers": split_semicolon(opening.get("layer")),
            "block_name": opening.get("block_name"),
            "annotation": opening.get("annotation"),
            "annotation_source": opening.get("annotation_source"),
            "dimensions": compact_dict(
                {
                    "width": round(width, 3) if width is not None else None,
                    "width_source": opening.get("width_source") or opening.get("size_source"),
                    "width_geometry_original": opening.get("width_geometry_original"),
                    "height": round(height, 3) if height is not None else None,
                    "height_source": opening.get("height_source"),
                    "sill_height": round(sill, 3) if sill is not None else None,
                    "sill_height_source": opening.get("sill_height_source"),
                }
            ),
            "host": compact_dict(
                {
                    "host_wall_id": host_wall_id,
                    "host_wall_run_id": host_wall_run_id,
                    "raw_host_wall_id": opening.get("host_wall_id"),
                    "raw_host_wall_run_id": opening.get("host_wall_run_id"),
                }
            ),
            "cross_view": compact_dict(
                {
                    "matched_elevation_drawing": opening.get("matched_elevation_drawing"),
                    "matched_elevation_opening_id": opening.get("matched_elevation_opening_id"),
                    "match_score": opening.get("cross_view_match_score"),
                    "match_status": opening.get("cross_view_match_status"),
                    "match_reason": opening.get("cross_view_match_reason"),
                }
            ),
            "nearby_raw_geometry": raw_items,
        }
    )


def nearby_raw_geometry(opening: dict, raw_rows: list[dict], radius: float = 1500.0, limit: int = 12) -> list[dict[str, Any]]:
    origin = point(opening.get("point") or opening.get("local_point"))
    if origin is None:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for raw in raw_rows:
        raw_pt = raw_geometry_point(raw)
        if raw_pt is None:
            continue
        dist = hypot(raw_pt[0] - origin[0], raw_pt[1] - origin[1])
        if dist > radius:
            continue
        scored.append((dist, raw_geometry_summary(raw, dist)))
    scored.sort(key=lambda item: item[0])
    return [item for _dist, item in scored[:limit]]


def raw_geometry_point(raw: dict) -> tuple[float, float] | None:
    center = coords(raw, "center")
    if center is not None:
        return center
    start = coords(raw, "start")
    end = coords(raw, "end")
    if start is not None and end is not None:
        return ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    return start or end


def raw_geometry_summary(raw: dict, distance_from_opening: float) -> dict[str, Any]:
    start = coords(raw, "start")
    end = coords(raw, "end")
    center = coords(raw, "center")
    return compact_dict(
        {
            "raw_id": raw.get("raw_geometry_id"),
            "type": raw.get("raw_type"),
            "layer": raw.get("layer"),
            "distance": round(distance_from_opening, 3),
            "start": point_list(start),
            "end": point_list(end),
            "center": point_list(center),
            "radius": smart_value(raw.get("radius")),
            "text": trim_text(raw.get("text"), 80),
            "block_name": raw.get("block_name"),
            "point_count": smart_value(raw.get("point_count")),
        }
    )


def coords(row: dict, prefix: str) -> tuple[float, float] | None:
    x = number(row.get(f"{prefix}_x"))
    y = number(row.get(f"{prefix}_y"))
    if x is None or y is None:
        return None
    return x, y


def point_list(value: tuple[float, float] | None) -> list[float] | None:
    if value is None:
        return None
    return [round(value[0], 3), round(value[1], 3)]


def trim_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def line_length(start: tuple[float, float] | None, end: tuple[float, float] | None) -> Any:
    if start is None or end is None:
        return ""
    return round(hypot(end[0] - start[0], end[1] - start[1]), 3)


def angle(start: tuple[float, float] | None, end: tuple[float, float] | None) -> Any:
    if start is None or end is None:
        return ""
    return round(degrees(atan2(end[1] - start[1], end[0] - start[0])), 3)


def point(value: object) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def value_at(value: tuple[float, float] | None, index: int) -> Any:
    if value is None:
        return ""
    return round(value[index], 3)


def format_points(points: object) -> str:
    if not isinstance(points, list):
        return ""
    formatted = []
    for item in points:
        pt = point(item)
        if pt is not None:
            formatted.append([round(pt[0], 3), round(pt[1], 3)])
    return json.dumps(formatted, ensure_ascii=False, separators=(",", ":")) if formatted else ""


def parse_points(value: object) -> list[dict[str, float]]:
    if not value:
        return []
    raw_points = value
    if isinstance(value, str):
        try:
            raw_points = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw_points, list):
        return []
    points: list[dict[str, float]] = []
    for item in raw_points:
        pt = point(item)
        if pt is not None:
            points.append({"x": round(pt[0], 3), "y": round(pt[1], 3), "z": 0.0})
    return points


def parse_json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def sort_point(value: object) -> tuple[float, float]:
    pt = point(value)
    if pt is None:
        return (0.0, 0.0)
    return (pt[0], pt[1])


def number(value: object) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def valid_number(value: object) -> bool:
    parsed = number(value)
    return parsed is not None and isfinite(parsed)


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return bool_text(value)
    return value


def count_by(rows: list[dict[str, Any]], key: str, value: object) -> int:
    return sum(1 for row in rows if row.get(key) == value)


def safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
    return cleaned or "Project"


def unique_dir(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.name}_{index}")
        if not candidate.exists():
            return candidate
        index += 1
