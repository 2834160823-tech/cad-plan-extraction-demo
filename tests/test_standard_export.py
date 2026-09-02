from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from cad_plan_demo.dxf_parser import parse_dxf
from cad_plan_demo.pipeline import analyze_entities
from cad_plan_demo.standard_export import (
    human_report_drawing_results,
    parse_parapet_height_text,
    uncertain_rows,
    validate_tables,
    write_standard_project_outputs,
)


class StandardExportTests(unittest.TestCase):
    def test_validation_ignores_none_numeric_and_elevation_opening_host(self) -> None:
        tables = {
            "Drawings.csv": [
                {"Drawing_ID": "DRAWING-001", "Drawing_Type": "floor_plan"},
                {"Drawing_ID": "DRAWING-002", "Drawing_Type": "elevation"},
            ],
            "Walls.csv": [],
            "Doors.csv": [{"Element_ID": "DOOR-ELEV", "Drawing_ID": "DRAWING-002", "Host_Wall_ID": ""}],
            "Windows.csv": [],
            "Floors.csv": [{"Element_ID": "FLOOR-001", "Thickness": None}],
        }

        errors = validate_tables(tables)

        self.assertFalse(any(row.get("Record_ID") in {"DOOR-ELEV", "FLOOR-001"} for row in errors))

    def test_uncertain_rows_keeps_only_floor_plan_recognition_issues(self) -> None:
        tables = {
            "Drawings.csv": [
                {"Drawing_ID": "DRAWING-001", "Drawing_Type": "floor_plan"},
                {"Drawing_ID": "DRAWING-002", "Drawing_Type": "elevation"},
            ],
            "Walls.csv": [
                {
                    "Element_ID": "WALL-001",
                    "Drawing_ID": "DRAWING-001",
                    "Needs_Review": "true",
                    "Confidence": 0.95,
                    "Remarks": "墙高未从图纸直接识别，使用默认墙高。",
                }
            ],
            "Doors.csv": [
                {
                    "Element_ID": "DOOR-ELEV",
                    "Drawing_ID": "DRAWING-002",
                    "Needs_Review": "true",
                    "Confidence": 0.6,
                }
            ],
            "Windows.csv": [
                {
                    "Element_ID": "WINDOW-PLAN",
                    "Drawing_ID": "DRAWING-001",
                    "Needs_Review": "true",
                    "Confidence": 0.62,
                }
            ],
        }

        rows = uncertain_rows(tables, [])

        self.assertEqual([row["Element_ID"] for row in rows], ["WINDOW-PLAN"])

    def test_human_report_adds_projected_stairwell_opening_to_host_floor_page(self) -> None:
        drawing_results = [("stair", minimal_result("楼梯详图", "architectural_plan")), ("level_two", minimal_result("二层平面图", "architectural_plan"))]
        tables = {
            "Floor_Openings.csv": [
                {
                    "Opening_ID": "FLOOROPENING-001",
                    "Drawing_ID": "DRAWING-002",
                    "Host_Floor_ID": "FLOOR-002",
                    "Opening_Type": "stairwell_opening",
                    "Boundary_Points": "[[100,200],[3100,200],[3100,5200],[100,5200]]",
                    "Center_X": 1600,
                    "Center_Y": 2700,
                    "Width": 3000,
                    "Depth": 5000,
                    "Source": "stair_boundary_projected_to_host_floor",
                    "Confidence": 0.9,
                    "Needs_Review": "true",
                }
            ]
        }

        report_results = human_report_drawing_results(drawing_results, tables)

        opening = report_results[1][1]["floor_openings"][0]
        self.assertEqual(opening["id"], "FLOOROPENING-001")
        self.assertEqual(opening["opening_type"], "stairwell_opening")
        self.assertEqual(opening["local_boundary_points"], [(100.0, 200.0), (3100.0, 200.0), (3100.0, 5200.0), (100.0, 5200.0)])

    def test_door_missing_height_uses_review_default_for_revit_handoff(self) -> None:
        result = {
            "axes": [],
            "walls": [],
            "wall_runs": [],
            "openings": [
                {
                    "id": "O001",
                    "kind": "door",
                    "point": (1000.0, 2000.0),
                    "local_point": (1000.0, 2000.0),
                    "width": 900.0,
                    "source": "quarter_arc",
                    "confidence": 0.78,
                    "component_category": "single_swing_door",
                }
            ],
            "columns": [],
            "floors": [],
            "roofs": [],
            "floor_openings": [],
            "stairs": [],
            "railings": [],
            "notes": {"text_items": []},
            "plan_summary": {"floor_heights": [{"height_mm": 3000.0}]},
        }

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, [("synthetic", result)], Path("synthetic.dxf"))
            with (export.csv_dir / "Doors.csv").open("r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Height"], "2100.0")
        self.assertEqual(rows[0]["Height_Source"], "door_default_2100_needs_review")
        self.assertEqual(rows[0]["Needs_Review"], "true")

    def test_standard_export_creates_required_files_and_headers(self) -> None:
        source = Path("examples/sample_requirements_plan.dxf")
        entities = parse_dxf(source)
        result = analyze_entities(entities, source, source)

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, [(source.stem, result)], source)
            csv_dir = export.csv_dir

            required = [
                "Manifest.csv",
                "Project_Info.csv",
                "Drawings.csv",
                "Levels.csv",
                "Grids.csv",
                "Walls.csv",
                "Wall_Runs.csv",
                "Doors.csv",
                "Windows.csv",
                "Columns.csv",
                "Floors.csv",
                "Roofs.csv",
                "Parapets.csv",
                "Floor_Openings.csv",
                "Railings.csv",
                "Raw_Geometry.csv",
                "Opening_Wall_Run_Map.csv",
                "Materials.csv",
                "Element_Material_Map.csv",
                "Uncertain_Elements.csv",
            ]
            for name in required:
                self.assertTrue((csv_dir / name).exists(), name)

            with (csv_dir / "Columns.csv").open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                header = next(reader)
            self.assertIn("Element_ID", header)
            self.assertIn("Column_Type", header)

            with (csv_dir / "Walls.csv").open("r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["Element_ID"], "WALL-001")
            self.assertEqual(rows[0]["Project_ID"], "PROJECT-001")

            with (csv_dir / "Roofs.csv").open("r", encoding="utf-8-sig", newline="") as f:
                roof_rows = list(csv.DictReader(f))
            with (csv_dir / "Parapets.csv").open("r", encoding="utf-8-sig", newline="") as f:
                parapet_rows = list(csv.DictReader(f))
            self.assertEqual(len(roof_rows), 1)
            self.assertEqual(roof_rows[0]["Roof_Type"], "flat_roof")
            self.assertEqual(len(parapet_rows), 4)
            self.assertEqual(parapet_rows[0]["Host_Roof_ID"], roof_rows[0]["Element_ID"])
            self.assertIn("Height_Source", parapet_rows[0])

            with (csv_dir / "Doors.csv").open("r", encoding="utf-8-sig", newline="") as f:
                door_rows = list(csv.DictReader(f))
            with (csv_dir / "Windows.csv").open("r", encoding="utf-8-sig", newline="") as f:
                window_rows = list(csv.DictReader(f))
            opening_rows = door_rows + window_rows
            self.assertTrue(opening_rows)
            self.assertIn("Final_Category", opening_rows[0])
            self.assertIn("Mechanical_Category", opening_rows[0])
            self.assertIn("Needs_AI_Classification", opening_rows[0])
            self.assertIn("Classification_Input", opening_rows[0])
            classification_input = json.loads(opening_rows[0]["Classification_Input"])
            self.assertTrue(classification_input["needs_ai_classification"])
            self.assertIn("mechanical_category_candidate", classification_input)

            self.assertTrue(export.human_report.exists())
            self.assertTrue(export.model_dir.is_dir())
            self.assertTrue(export.ai_model.exists())
            self.assertTrue(export.ai_elements.exists())
            self.assertTrue(export.detailed_report.exists())
            self.assertEqual(export.csv_dir.parent, export.model_dir)

            model = json.loads(export.ai_model.read_text(encoding="utf-8"))
            self.assertEqual(model["schema_version"], "1.0")
            self.assertEqual(model["exporter_version"], "standard-export-1.4")
            self.assertEqual(model["summary"]["drawings"], 1)
            self.assertIn("wall_runs", model["summary"])
            self.assertIn("railings", model["summary"])
            self.assertEqual(model["summary"]["roofs"], 1)
            self.assertEqual(model["summary"]["parapets"], 4)
            self.assertIn("material_catalog", model)
            self.assertIn("material_links", model)
            self.assertIn("material_linking", model)
            self.assertIn("elements", model["drawings"][0])

            element_lines = export.ai_elements.read_text(encoding="utf-8").splitlines()
            self.assertTrue(element_lines)
            self.assertIn("record_type", json.loads(element_lines[0]))
            element_records = [json.loads(line) for line in element_lines]
            material_ready = [
                record
                for record in element_records
                if record.get("element_type") in {"wall", "door", "window"} and "material" in record
            ]
            self.assertTrue(material_ready)
            self.assertEqual(material_ready[0]["material"]["status"], "unassigned")
            opening_records = [record for record in element_records if record.get("element_type") in {"door", "window"}]
            self.assertTrue(opening_records)
            self.assertIn("mechanical_classification", opening_records[0])
            self.assertIn("classification_input", opening_records[0])
            self.assertTrue(opening_records[0]["mechanical_classification"]["needs_ai_classification"])
            self.assertNotIn("category", opening_records[0])
            roof_records = [record for record in element_records if record.get("element_type") == "roof"]
            parapet_records = [record for record in element_records if record.get("element_type") == "parapet"]
            self.assertEqual(len(roof_records), 1)
            self.assertEqual(len(parapet_records), 4)

            self.assertEqual(
                sorted(item.name for item in export.output_dir.iterdir()),
                [
                    "01_人工快速查看_中文识别报告.xlsx",
                    "02_标准化模型数据",
                    "03_人工详细核查_完整报告.md",
                ],
            )

    def test_standard_export_infers_levels_from_drawing_titles(self) -> None:
        drawing_results = [
            ("one", minimal_result("一层平面图", "architectural_plan")),
            ("two", minimal_result("二层平面图", "architectural_plan")),
            ("roof", minimal_result("屋顶平面图", "architectural_plan")),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, drawing_results, Path("synthetic_levels.dxf"))
            with (export.csv_dir / "Levels.csv").open("r", encoding="utf-8-sig", newline="") as f:
                levels = list(csv.DictReader(f))
            with (export.csv_dir / "Drawings.csv").open("r", encoding="utf-8-sig", newline="") as f:
                drawings = list(csv.DictReader(f))

        self.assertEqual([row["Level_Name"] for row in levels], ["一层", "二层", "屋顶层"])
        self.assertEqual([row["Elevation"] for row in levels], ["0.0", "3000.0", "6000.0"])
        self.assertEqual(drawings[0]["Level_ID"], levels[0]["Level_ID"])
        self.assertEqual(drawings[1]["Level_ID"], levels[1]["Level_ID"])
        self.assertEqual(drawings[2]["Level_ID"], levels[2]["Level_ID"])

    def test_standard_export_infers_levels_from_raw_title_candidates(self) -> None:
        one = minimal_result("平面图", "architectural_plan")
        one["notes"]["drawing_title_candidates"] = [{"text": "平面图", "raw_text": "一层平面图"}]
        two = minimal_result("平面图", "architectural_plan")
        two["notes"]["drawing_title_candidates"] = [{"text": "平面图", "raw_text": "二层平面图"}]

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, [("F001", one), ("F002", two)], Path("synthetic_levels.dxf"))
            with (export.csv_dir / "Levels.csv").open("r", encoding="utf-8-sig", newline="") as f:
                levels = list(csv.DictReader(f))
            with (export.csv_dir / "Drawings.csv").open("r", encoding="utf-8-sig", newline="") as f:
                drawings = list(csv.DictReader(f))

        self.assertEqual([row["Level_Name"] for row in levels], ["一层", "二层"])
        self.assertEqual([row["Drawing_Name"] for row in drawings], ["一层平面图", "二层平面图"])
        self.assertNotEqual(drawings[0]["Level_ID"], drawings[1]["Level_ID"])

    def test_frame_number_does_not_create_false_level(self) -> None:
        one = minimal_result("一层平面图", "architectural_plan")
        two = minimal_result("二层平面图", "architectural_plan")
        elevation = minimal_result("南立面图", "architectural_elevation")
        elevation["notes"]["drawing_title_candidates"] = [{"text": "南立面图", "raw_text": "南立面图"}]

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(
                tmp,
                [("Test4_F001", one), ("Test4_F002", two), ("Test4_F003", elevation)],
                Path("Test4.dxf"),
            )
            with (export.csv_dir / "Levels.csv").open("r", encoding="utf-8-sig", newline="") as f:
                levels = list(csv.DictReader(f))
            with (export.csv_dir / "Drawings.csv").open("r", encoding="utf-8-sig", newline="") as f:
                drawings = list(csv.DictReader(f))

        self.assertEqual([row["Level_Name"] for row in levels], ["一层", "二层"])
        self.assertEqual(drawings[2]["Level_ID"], "")
        self.assertEqual(drawings[2]["Needs_Review"], "true")

    def test_text_annotations_are_associated_for_space_and_material_agents(self) -> None:
        result = minimal_result("一层平面图", "architectural_plan")
        result["notes"]["text_items"] = [
            {"text": "客厅", "point": (500, 120), "layer": "A-TEXT", "height": 250, "rotation": 0},
            {"text": "外墙材料：加气混凝土砌块", "point": (500, 220), "layer": "A-TEXT", "height": 250, "rotation": 0},
        ]
        result["walls"] = [
            {
                "id": "W0001",
                "start": (0, 0),
                "end": (1000, 0),
                "length": 1000,
                "normalized_width": 200,
                "height_mm": 3000,
                "confidence": 0.9,
                "source_layers": ["A-WALL"],
                "needs_review": False,
                "remarks": "",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, [("plan", result)], Path("synthetic_text_assoc.dxf"))
            with (export.csv_dir / "Text_Annotations.csv").open("r", encoding="utf-8-sig", newline="") as f:
                texts = list(csv.DictReader(f))
            model = json.loads(export.ai_model.read_text(encoding="utf-8"))
            element_records = [json.loads(line) for line in export.ai_elements.read_text(encoding="utf-8").splitlines()]

        associated = [row for row in texts if row["Associated_Element_ID"]]
        self.assertTrue(associated)
        self.assertEqual(associated[0]["Associated_Element_ID"], "WALL-001")
        self.assertEqual(associated[0]["Associated_Element_Type"], "wall")
        self.assertEqual(associated[0]["Association_Method"], "nearest_line")
        self.assertIn("semantic_context", model)
        self.assertTrue(model["semantic_context"]["space_semantic_inputs"]["candidate_texts"])
        self.assertTrue(model["semantic_context"]["material_semantic_inputs"]["candidate_texts"])
        text_records = [record for record in element_records if record.get("annotation_type") == "text"]
        self.assertTrue(any(record.get("associated_element", {}).get("element_id") == "WALL-001" for record in text_records))

    def test_parapet_height_text_overrides_default_height(self) -> None:
        self.assertEqual(parse_parapet_height_text("\u5973\u513f\u5899\u9ad80.6m"), 600)
        result = minimal_result("\u5c4b\u9876\u5e73\u9762\u56fe", "architectural_plan")
        result["notes"]["text_items"] = [
            {"text": "\u5973\u513f\u5899\u9ad80.6m", "point": (100, 100), "layer": "A-TEXT", "height": 250, "rotation": 0}
        ]
        result["floors"] = [
            {
                "id": "FLOOR-A",
                "floor_type": "default_floor_slab",
                "boundary_points": [(0, 0), (6000, 0), (6000, 4000), (0, 4000)],
                "area": 24000000,
                "thickness_mm": 120,
                "elevation_mm": 6600,
                "source": "test",
                "confidence": 0.9,
                "needs_review": False,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, [("roof", result)], Path("synthetic_parapet_height.dxf"))
            with (export.csv_dir / "Parapets.csv").open("r", encoding="utf-8-sig", newline="") as f:
                parapets = list(csv.DictReader(f))
            element_records = [json.loads(line) for line in export.ai_elements.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(parapets), 4)
        self.assertEqual(parapets[0]["Height"], "600.0")
        self.assertIn("text_annotation", parapets[0]["Height_Source"])
        self.assertEqual(parapets[0]["Needs_Review"], "false")
        parapet_records = [record for record in element_records if record.get("element_type") == "parapet"]
        self.assertEqual(parapet_records[0]["dimension_sources"]["height"], parapets[0]["Height_Source"])

    def test_parapet_height_uses_top_elevation_minus_roof_level(self) -> None:
        result = minimal_result("\u4e8c\u5c42\u5e73\u9762\u56fe", "architectural_plan")
        result["plan_summary"]["elevation_marks"] = [
            {"label": None, "elevation_mm": 0, "raw_text": "0.000", "point": (0, 0), "confidence": 0.68},
            {"label": None, "elevation_mm": 3300, "raw_text": "3.300", "point": (0, 3300), "confidence": 0.68},
            {"label": None, "elevation_mm": 6600, "raw_text": "6.600", "point": (0, 6600), "confidence": 0.68},
        ]
        result["plan_summary"]["floor_heights"] = [
            {"floor": None, "height_mm": 3300, "raw_text": "Derived from elevation marks", "point": (0, 3300), "confidence": 0.72}
        ]
        result["floors"] = [
            {
                "id": "FLOOR-B",
                "floor_type": "default_floor_slab",
                "boundary_points": [(0, 0), (12000, 0), (12000, 12000), (0, 12000)],
                "area": 144000000,
                "thickness_mm": 120,
                "elevation_mm": 3300,
                "source": "test",
                "confidence": 0.9,
                "needs_review": False,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, [("Test4_F002", result)], Path("synthetic_parapet_elevation.dxf"))
            with (export.csv_dir / "Parapets.csv").open("r", encoding="utf-8-sig", newline="") as f:
                parapets = list(csv.DictReader(f))

        self.assertEqual(parapets[0]["Height"], "3300.0")
        self.assertEqual(parapets[0]["Height_Source"], "elevation_mark_difference:top=6600;base=3300")
        self.assertEqual(parapets[0]["Needs_Review"], "false")


def minimal_result(title: str, drawing_type: str) -> dict:
    return {
        "notes": {
            "drawing_title": title,
            "drawing_type": drawing_type,
            "drawing_title_confidence": 0.9,
            "text_items": [],
        },
        "plan_summary": {"floor_heights": [], "elevation_marks": []},
        "coordinate_system": {},
        "counts": {},
        "input": {},
    }


if __name__ == "__main__":
    unittest.main()
