from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from cad_plan_demo.dxf_parser import CadEntity
from cad_plan_demo.excel_export import human_floor_opening_rows
from cad_plan_demo.pipeline import analyze_entities
from cad_plan_demo.standard_export import write_standard_project_outputs
from cad_plan_demo.stair_recognition import enrich_stairs_with_project_floor_height


class StairRecognitionTests(unittest.TestCase):
    def test_double_run_stair_detail_parameters_are_recognized(self) -> None:
        result = analyze_entities(stair_detail_entities(), Path("synthetic_stair_detail.dxf"), Path("synthetic_stair_detail.dxf"))

        self.assertEqual(result["counts"]["stairs"], 1)
        stair = result["stairs"][0]
        self.assertEqual(stair["stair_type"], "double_run_stair")
        self.assertEqual(stair["start_level"], "一层")
        self.assertEqual(stair["end_level"], "二层")
        self.assertEqual(stair["total_rise_mm"], 3600)
        self.assertEqual(stair["riser_height_mm"], 150)
        self.assertEqual(stair["tread_depth_mm"], 300)
        self.assertEqual(stair["number_of_risers"], 24)
        self.assertEqual(stair["number_of_treads"], 22)
        self.assertEqual(stair["width_mm"], 1200)
        self.assertEqual(stair["run_count"], 2)
        self.assertEqual(stair["risers_per_run"], 12)
        self.assertEqual(stair["treads_per_run"], 11)
        self.assertEqual(stair["run_length_mm"], 3300)
        self.assertEqual(stair["landing_length_mm"], 1200)
        self.assertEqual(stair["landing_width_mm"], 1200)
        self.assertEqual(
            stair["boundary_points"],
            [(1000.0, 1000.0), (4600.0, 1000.0), (4600.0, 2200.0), (1000.0, 2200.0)],
        )
        self.assertEqual(stair["stairwell_opening_boundary"], stair["boundary_points"])
        self.assertTrue(stair["opening_required"])

    def test_stair_detail_can_be_inferred_from_step_geometry_and_dimensions(self) -> None:
        result = analyze_entities(
            stair_geometry_and_dimension_entities(),
            Path("synthetic_stair_geometry.dxf"),
            Path("synthetic_stair_geometry.dxf"),
        )

        self.assertEqual(result["counts"]["stairs"], 1)
        stair = result["stairs"][0]
        self.assertEqual(stair["tread_depth_mm"], 220)
        self.assertEqual(stair["riser_height_mm"], 150)
        self.assertEqual(stair["width_mm"], 1200)
        self.assertEqual(stair["landing_length_mm"], 1200)
        self.assertEqual(stair["landing_width_mm"], 1200)
        self.assertEqual(stair["number_of_risers"], 22)
        self.assertEqual(stair["number_of_treads"], 20)
        self.assertEqual(stair["risers_per_run"], 11)
        self.assertEqual(stair["treads_per_run"], 10)
        self.assertEqual(stair["run_length_mm"], 2200)
        self.assertEqual(stair["total_rise_mm"], 3300)
        self.assertTrue(stair["opening_required"])

    def test_total_rise_dimension_fills_missing_geometric_step(self) -> None:
        result = analyze_entities(
            stair_geometry_and_dimension_entities(upper_run_steps=10),
            Path("synthetic_stair_total_rise_fill.dxf"),
            Path("synthetic_stair_total_rise_fill.dxf"),
        )

        stair = result["stairs"][0]
        self.assertEqual(stair["riser_height_mm"], 150)
        self.assertEqual(stair["total_rise_mm"], 3300)
        self.assertEqual(stair["number_of_risers"], 22)
        self.assertEqual(stair["number_of_treads"], 20)
        self.assertEqual(stair["risers_per_run"], 11)
        self.assertEqual(stair["treads_per_run"], 10)

    def test_step_count_prefers_tread_horizontal_lines(self) -> None:
        entities = stair_geometry_and_dimension_entities(include_total_rise_dimension=False)
        entities.extend(
            [
                CadEntity("LINE", "A-STAIR", {"start": (5200, 1000), "end": (5200, 1150)}),
                CadEntity("LINE", "A-STAIR", {"start": (5400, 1000), "end": (5400, 1150)}),
                CadEntity("LINE", "A-STAIR", {"start": (5600, 1000), "end": (5600, 1150)}),
            ]
        )

        result = analyze_entities(
            entities,
            Path("synthetic_stair_horizontal_count.dxf"),
            Path("synthetic_stair_horizontal_count.dxf"),
        )

        stair = result["stairs"][0]
        self.assertEqual(stair["number_of_risers"], 22)
        self.assertEqual(stair["number_of_treads"], 20)
        self.assertIn("踏步", stair["remarks"])

    def test_plan_transverse_horizontal_lines_count_as_treads(self) -> None:
        entities = stair_plan_horizontal_line_entities(22)

        result = analyze_entities(
            entities,
            Path("synthetic_stair_plan_lines.dxf"),
            Path("synthetic_stair_plan_lines.dxf"),
        )

        stair = result["stairs"][0]
        self.assertEqual(stair["number_of_risers"], 22)
        self.assertEqual(stair["number_of_treads"], 20)
        self.assertEqual(stair["risers_per_run"], 11)
        self.assertEqual(stair["treads_per_run"], 10)
        self.assertEqual(stair["width_mm"], 1200)
        self.assertIn("踏步", stair["remarks"])

    def test_double_run_plan_infers_stairwell_width_between_runs(self) -> None:
        result = analyze_entities(
            stair_plan_with_middle_well_entities(),
            Path("synthetic_stairwell_width.dxf"),
            Path("synthetic_stairwell_width.dxf"),
        )

        stair = result["stairs"][0]
        self.assertEqual(stair["width_mm"], 1200)
        self.assertEqual(stair["stairwell_width_mm"], 300)

    def test_double_run_plan_infers_repeated_inner_gap_as_stairwell_width(self) -> None:
        result = analyze_entities(
            stair_plan_with_repeated_inner_gap_entities(),
            Path("synthetic_repeated_stairwell_width.dxf"),
            Path("synthetic_repeated_stairwell_width.dxf"),
        )

        stair = result["stairs"][0]
        self.assertEqual(stair["width_mm"], 1200)
        self.assertEqual(stair["stairwell_width_mm"], 100)

    def test_double_run_plan_infers_overlapping_platform_gap_as_stairwell_width(self) -> None:
        result = analyze_entities(
            stair_plan_with_platform_gap_entities(),
            Path("synthetic_platform_stairwell_width.dxf"),
            Path("synthetic_platform_stairwell_width.dxf"),
        )

        stair = result["stairs"][0]
        self.assertEqual(stair["stairwell_width_mm"], 100)

    def test_large_nearby_dimension_does_not_override_riser_height(self) -> None:
        entities = stair_plan_with_repeated_inner_gap_entities()
        entities.append(CadEntity("DIMENSION", "A-DIM", {"point": (3600, 1800), "measurement": 1000, "start": (3600, 1000), "end": (3600, 2000)}))

        result = analyze_entities(
            entities,
            Path("synthetic_large_dimension_near_stair.dxf"),
            Path("synthetic_large_dimension_near_stair.dxf"),
        )

        stair = result["stairs"][0]
        self.assertNotEqual(stair["riser_height_mm"], 1000)
        self.assertNotEqual(stair["total_rise_mm"], 22000)

    def test_project_floor_height_fills_missing_geometric_step(self) -> None:
        result = analyze_entities(
            stair_geometry_and_dimension_entities(upper_run_steps=10, include_total_rise_dimension=False),
            Path("synthetic_stair_project_height_fill.dxf"),
            Path("synthetic_stair_project_height_fill.dxf"),
        )
        result["stairs"][0]["number_of_risers"] = 21
        result["stairs"][0]["number_of_treads"] = 19
        result["stairs"][0]["total_rise_mm"] = 3150
        level_result = {"plan_summary": {"floor_heights": [{"height_mm": 3300}]}, "stairs": []}

        enrich_stairs_with_project_floor_height([("level_plan", level_result), ("stair_detail", result)])

        stair = result["stairs"][0]
        self.assertEqual(stair["total_rise_mm"], 3300)
        self.assertEqual(stair["number_of_risers"], 22)
        self.assertEqual(stair["number_of_treads"], 20)
        self.assertEqual(stair["risers_per_run"], 11)
        self.assertEqual(stair["treads_per_run"], 10)

    def test_project_riser_height_overrides_plan_misread(self) -> None:
        good = stair_result_without_project_span()
        bad = stair_result_without_project_span()
        bad["stairs"][0]["id"] = "ST002"
        bad["stairs"][0]["riser_height_mm"] = 220
        bad["stairs"][0]["tread_depth_mm"] = 250
        bad["stairs"][0]["number_of_risers"] = 24
        bad["stairs"][0]["number_of_treads"] = 22
        bad["stairs"][0]["total_rise_mm"] = 5280

        enrich_stairs_with_project_floor_height([("good_stair", good), ("bad_stair", bad)])

        stair = bad["stairs"][0]
        self.assertEqual(stair["riser_height_mm"], 150)
        self.assertEqual(stair["total_rise_mm"], 3600)
        self.assertEqual(stair["number_of_risers"], 24)
        self.assertEqual(stair["risers_per_run"], 12)
        self.assertEqual(stair["treads_per_run"], 11)

    def test_recognition_keeps_one_adjacent_segment_in_three_floor_project(self) -> None:
        stair_result = stair_result_without_project_span()
        project_results = [
            ("stair_detail", stair_result),
            ("F001", plan_result_for_floor("\u4e00\u5c42\u5e73\u9762\u56fe", 3300)),
            ("F002", plan_result_for_floor("\u4e8c\u5c42\u5e73\u9762\u56fe")),
            ("F003", plan_result_for_floor("\u4e09\u5c42\u5e73\u9762\u56fe")),
        ]

        enrich_stairs_with_project_floor_height(project_results)

        stair = stair_result["stairs"][0]
        self.assertIsNone(stair["start_level"])
        self.assertIsNone(stair["end_level"])
        self.assertNotIn("level_span_count", stair)
        self.assertEqual(stair["run_count"], 2)
        self.assertEqual(stair["total_rise_mm"], 3300)
        self.assertEqual(stair["number_of_risers"], 22)
        self.assertEqual(stair["number_of_treads"], 20)
        self.assertEqual(stair["risers_per_run"], 11)
        self.assertEqual(stair["treads_per_run"], 10)

    def test_detail_stairwell_opening_is_not_projected_without_floor_evidence(self) -> None:
        stair_result = stair_result_without_project_span()
        project_results = [
            ("stair_detail", stair_result),
            ("F001", plan_result_for_floor("\u4e00\u5c42\u5e73\u9762\u56fe", 3300, with_floor=True)),
            ("F002", plan_result_for_floor("\u4e8c\u5c42\u5e73\u9762\u56fe", with_floor=True)),
            ("F003", plan_result_for_floor("\u4e09\u5c42\u5e73\u9762\u56fe", with_floor=True)),
        ]
        enrich_stairs_with_project_floor_height(project_results)

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, project_results, Path("synthetic_three_floor_stair.dxf"))
            with (export.csv_dir / "Stairs.csv").open("r", encoding="utf-8-sig", newline="") as f:
                stairs = list(csv.DictReader(f))
            with (export.csv_dir / "Floor_Openings.csv").open("r", encoding="utf-8-sig", newline="") as f:
                floor_openings = list(csv.DictReader(f))

        stairwell_openings = [row for row in floor_openings if row["Opening_Type"] == "stairwell_opening"]
        self.assertEqual(stairs[0]["Run_Count"], "2")
        self.assertEqual(stairs[0]["Total_Rise"], "3300.0")
        self.assertEqual(len(stairwell_openings), 1)
        self.assertEqual(stairwell_openings[0]["Host_Floor_ID"], "")
        self.assertNotEqual(stairwell_openings[0]["Source"], "stair_boundary_projected_to_host_floor")

    def test_multilevel_total_rise_exports_adjacent_two_run_segments(self) -> None:
        stair_result = stair_result_without_project_span()
        stair_result["stairs"][0]["total_rise_mm"] = 6600
        stair_result["stairs"][0]["number_of_risers"] = 44
        stair_result["stairs"][0]["number_of_treads"] = 40
        project_results = [
            ("stair_detail", stair_result),
            ("F001", plan_result_for_floor("\u4e00\u5c42\u5e73\u9762\u56fe", 3300, with_floor=True)),
            ("F002", plan_result_for_floor("\u4e8c\u5c42\u5e73\u9762\u56fe", with_floor=True)),
            ("F003", plan_result_for_floor("\u4e09\u5c42\u5e73\u9762\u56fe", with_floor=True)),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, project_results, Path("synthetic_two_level_span_stair.dxf"))
            with (export.csv_dir / "Stairs.csv").open("r", encoding="utf-8-sig", newline="") as f:
                stairs = list(csv.DictReader(f))

        self.assertEqual(len(stairs), 2)
        self.assertEqual([row["Run_Count"] for row in stairs], ["2", "2"])
        self.assertEqual([row["Total_Rise"] for row in stairs], ["3300.0", "3300.0"])
        self.assertEqual([row["Number_Of_Risers"] for row in stairs], ["22", "22"])
        self.assertEqual([row["Start_Level_ID"] for row in stairs], ["LEVEL-001", "LEVEL-002"])
        self.assertEqual([row["End_Level_ID"] for row in stairs], ["LEVEL-002", "LEVEL-003"])
        self.assertEqual([row["Stair_Segment_Number"] for row in stairs], ["1", "2"])

    def test_five_floor_project_does_not_expand_one_detail_into_eight_runs(self) -> None:
        stair_result = stair_result_without_project_span()
        project_results = [
            ("stair_detail", stair_result),
            ("F001", plan_result_for_floor("\u4e00\u5c42\u5e73\u9762\u56fe", 3300, with_floor=True)),
            ("F002", plan_result_for_floor("\u4e8c\u5c42\u5e73\u9762\u56fe", with_floor=True)),
            ("F003", plan_result_for_floor("\u4e09\u5c42\u5e73\u9762\u56fe", with_floor=True)),
            ("F004", plan_result_for_floor("\u56db\u5c42\u5e73\u9762\u56fe", with_floor=True)),
            ("F005", plan_result_for_floor("\u4e94\u5c42\u5e73\u9762\u56fe", with_floor=True)),
        ]
        enrich_stairs_with_project_floor_height(project_results)

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, project_results, Path("synthetic_five_floor_stair.dxf"))
            with (export.csv_dir / "Stairs.csv").open("r", encoding="utf-8-sig", newline="") as f:
                stairs = list(csv.DictReader(f))
            with (export.csv_dir / "Floor_Openings.csv").open("r", encoding="utf-8-sig", newline="") as f:
                floor_openings = list(csv.DictReader(f))

        stair = stairs[0]
        stairwell_openings = [row for row in floor_openings if row["Opening_Type"] == "stairwell_opening"]
        self.assertEqual(stair["Start_Level_ID"], "")
        self.assertEqual(stair["End_Level_ID"], "")
        self.assertEqual(stair["Total_Rise"], "3300.0")
        self.assertEqual(stair["Run_Count"], "2")
        self.assertEqual(stair["Risers_Per_Run"], "11")
        self.assertEqual(stair["Treads_Per_Run"], "10")
        self.assertEqual(stair["Number_Of_Risers"], "22")
        self.assertEqual(stair["Number_Of_Treads"], "20")
        self.assertEqual(len(stairwell_openings), 1)
        self.assertEqual(stairwell_openings[0]["Host_Floor_ID"], "")
        self.assertNotEqual(stairwell_openings[0]["Source"], "stair_boundary_projected_to_host_floor")

    def test_standard_export_writes_stair_rows_and_ai_records(self) -> None:
        result = analyze_entities(stair_detail_entities(), Path("synthetic_stair_detail.dxf"), Path("synthetic_stair_detail.dxf"))
        result["floors"] = [
            {
                "id": "FLOOR0001",
                "floor_type": "default_floor_slab",
                "local_boundary_points": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]],
                "area": 24000000,
                "thickness_mm": 120,
                "elevation_mm": 0,
                "source": "test_floor",
                "confidence": 0.9,
                "opening_ids": [],
                "opening_count": 0,
                "needs_review": False,
                "remarks": "",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, [("stair_detail", result)], Path("synthetic_stair_detail.dxf"))
            with (export.csv_dir / "Stairs.csv").open("r", encoding="utf-8-sig", newline="") as f:
                stairs = list(csv.DictReader(f))
            with (export.csv_dir / "Floor_Openings.csv").open("r", encoding="utf-8-sig", newline="") as f:
                floor_openings = list(csv.DictReader(f))
            model = json.loads(export.ai_model.read_text(encoding="utf-8"))
            element_records = [json.loads(line) for line in export.ai_elements.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(stairs), 1)
        self.assertEqual(stairs[0]["Stair_Type"], "double_run_stair")
        self.assertTrue(json.loads(stairs[0]["Boundary_Points"]))
        self.assertTrue(json.loads(stairs[0]["Stairwell_Opening_Boundary"]))
        self.assertEqual(stairs[0]["Opening_Required"], "true")
        self.assertEqual(float(stairs[0]["Total_Rise"]), 3600)
        self.assertEqual(stairs[0]["Number_Of_Risers"], "24")
        self.assertEqual(stairs[0]["Number_Of_Treads"], "22")
        self.assertEqual(stairs[0]["Run_Count"], "2")
        self.assertEqual(stairs[0]["Risers_Per_Run"], "12")
        self.assertEqual(stairs[0]["Treads_Per_Run"], "11")
        self.assertEqual(float(stairs[0]["Landing_Length"]), 1200)
        self.assertIn("Stairwell_Width", stairs[0])
        self.assertEqual(len(floor_openings), 1)
        self.assertEqual(floor_openings[0]["Opening_Type"], "stairwell_opening")
        self.assertEqual(floor_openings[0]["Host_Floor_ID"], "FLOOR-001")
        self.assertEqual(model["summary"]["stairs"], 1)
        stair_records = [record for record in element_records if record.get("element_type") == "stair"]
        self.assertEqual(len(stair_records), 1)
        self.assertEqual(stair_records[0]["dimensions"]["riser_height"], 150)
        self.assertTrue(stair_records[0]["boundary"])
        self.assertTrue(stair_records[0]["stairwell_opening"]["opening_required"])
        self.assertTrue(stair_records[0]["stairwell_opening"]["boundary"])

    def test_standard_export_writes_stairwell_opening_without_host_floor_for_review(self) -> None:
        result = {
            "notes": {"drawing_type": "architectural_detail", "text_items": []},
            "coordinate_system": {"origin": [0, 0]},
            "plan_summary": {"floor_heights": []},
            "axes": [],
            "walls": [],
            "wall_runs": [],
            "openings": [],
            "columns": [],
            "floors": [],
            "floor_openings": [],
            "stairs": [
                {
                    "id": "ST001",
                    "stair_type": "double_run_stair",
                    "start": (0, 0),
                    "end": (4000, 0),
                    "boundary_points": [(0, 0), (4000, 0), (4000, 3000), (0, 3000)],
                    "stairwell_opening_boundary": [(0, 0), (4000, 0), (4000, 3000), (0, 3000)],
                    "opening_required": True,
                    "confidence": 0.82,
                }
            ],
            "railings": [],
            "dimensions": [],
            "raw_geometry": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, [("stair_detail", result)], Path("stair_detail.dxf"))
            with (export.csv_dir / "Floor_Openings.csv").open("r", encoding="utf-8-sig", newline="") as f:
                floor_openings = list(csv.DictReader(f))

        self.assertEqual(len(floor_openings), 1)
        self.assertEqual(floor_openings[0]["Opening_Type"], "stairwell_opening")
        self.assertEqual(floor_openings[0]["Host_Floor_ID"], "")
        self.assertEqual(floor_openings[0]["Needs_Review"], "true")
        self.assertIn("host floor needs mapping", floor_openings[0]["Remarks"])

    def test_human_report_lists_stairwell_opening_markers(self) -> None:
        result = analyze_entities(stair_detail_entities(), Path("synthetic_stair_detail.dxf"), Path("synthetic_stair_detail.dxf"))

        rows = human_floor_opening_rows(result)

        self.assertEqual(rows[0]["marker"], "楼梯洞口-001")
        self.assertEqual(rows[0]["opening_type"], "stairwell_opening")
        self.assertEqual(rows[0]["related_stair_id"], "STAIR0001")


def stair_detail_entities() -> list[CadEntity]:
    return [
        CadEntity("TEXT", "A-TITLE", {"point": (0, 5200), "height": 300, "text": "楼梯详图"}),
        CadEntity("TEXT", "A-STAIR-TEXT", {"point": (1000, 4600), "height": 220, "text": "楼梯 12级 一层-二层 层高3.6m 踏步300x150"}),
        CadEntity(
            "LWPOLYLINE",
            "A-STAIR",
            {"closed": True, "points": [(1000, 1000), (4600, 1000), (4600, 2200), (1000, 2200)]},
        ),
        CadEntity("LINE", "A-STAIR", {"start": (1300, 1000), "end": (1300, 2200)}),
        CadEntity("LINE", "A-STAIR", {"start": (1600, 1000), "end": (1600, 2200)}),
        CadEntity("LINE", "A-STAIR", {"start": (1900, 1000), "end": (1900, 2200)}),
    ]


def stair_geometry_and_dimension_entities(
    upper_run_steps: int = 10,
    include_total_rise_dimension: bool = True,
) -> list[CadEntity]:
    entities = [
        CadEntity("TEXT", "A-TITLE", {"point": (0, 6200), "height": 300, "text": "楼梯详图"}),
        CadEntity("DIMENSION", "A-DIM", {"point": (500, 4300), "measurement": 220, "start": (1000, 1000), "end": (1220, 1000)}),
        CadEntity("DIMENSION", "A-DIM", {"point": (500, 4000), "measurement": 150, "start": (1000, 1000), "end": (1000, 1150)}),
        CadEntity("LINE", "A-STAIR", {"start": (1000, 1000), "end": (2200, 1000)}),
        CadEntity("LINE", "A-STAIR", {"start": (2200, 1000), "end": (2200, 2200)}),
        CadEntity("LINE", "A-STAIR", {"start": (2200, 2200), "end": (1000, 2200)}),
        CadEntity("LINE", "A-STAIR", {"start": (1000, 2200), "end": (1000, 1000)}),
    ]
    if include_total_rise_dimension:
        entities.append(
            CadEntity(
                "DIMENSION",
                "A-DIM",
                {"point": (500, 3700), "measurement": 3300, "start": (1000, 1000), "end": (1000, 4300)},
            )
        )
    x = 1000
    y = 2500
    for _ in range(upper_run_steps):
        entities.append(CadEntity("LINE", "A-STAIR", {"start": (x, y), "end": (x + 220, y)}))
        entities.append(CadEntity("LINE", "A-STAIR", {"start": (x + 220, y), "end": (x + 220, y + 150)}))
        x += 220
        y += 150
    x = 1000
    y = 5000
    for _ in range(10):
        entities.append(CadEntity("LINE", "A-STAIR", {"start": (x, y), "end": (x + 220, y)}))
        entities.append(CadEntity("LINE", "A-STAIR", {"start": (x + 220, y), "end": (x + 220, y - 150)}))
        x += 220
        y -= 150
    return entities


def stair_plan_horizontal_line_entities(tread_count: int) -> list[CadEntity]:
    entities = [
        CadEntity("TEXT", "A-TITLE", {"point": (0, 5000), "height": 300, "text": "stair plan"}),
        CadEntity("DIMENSION", "A-DIM", {"point": (3500, 1200), "measurement": 220, "start": (1000, 1000), "end": (1000, 1220)}),
        CadEntity("DIMENSION", "A-DIM", {"point": (4000, 1200), "measurement": 150, "start": (1000, 1000), "end": (1000, 1150)}),
        CadEntity("LINE", "A-STAIR", {"start": (1000, 900), "end": (1000, 6000)}),
        CadEntity("LINE", "A-STAIR", {"start": (2200, 900), "end": (2200, 6000)}),
    ]
    y = 1000
    for _ in range(tread_count):
        entities.append(CadEntity("LINE", "A-STAIR", {"start": (1000, y), "end": (2200, y)}))
        y += 220
    return entities


def stair_plan_with_middle_well_entities() -> list[CadEntity]:
    return [
        CadEntity("TEXT", "A-TITLE", {"point": (0, 6500), "height": 300, "text": "stair plan"}),
        CadEntity("LINE", "A-STAIR", {"start": (1000, 1000), "end": (1000, 5000)}),
        CadEntity("LINE", "A-STAIR", {"start": (2200, 1000), "end": (2200, 5000)}),
        CadEntity("LINE", "A-STAIR", {"start": (2500, 1000), "end": (2500, 5000)}),
        CadEntity("LINE", "A-STAIR", {"start": (3700, 1000), "end": (3700, 5000)}),
        CadEntity("LINE", "A-STAIR", {"start": (1000, 1000), "end": (2200, 1000)}),
        CadEntity("LINE", "A-STAIR", {"start": (1000, 5000), "end": (2200, 5000)}),
        CadEntity("LINE", "A-STAIR", {"start": (2500, 1000), "end": (3700, 1000)}),
        CadEntity("LINE", "A-STAIR", {"start": (2500, 5000), "end": (3700, 5000)}),
    ]


def stair_plan_with_repeated_inner_gap_entities() -> list[CadEntity]:
    entities = [CadEntity("TEXT", "A-TITLE", {"point": (0, 5000), "height": 300, "text": "stair plan"})]
    for y in range(1000, 2760, 220):
        entities.append(CadEntity("LINE", "A-STAIR", {"start": (1000, y), "end": (2200, y)}))
        entities.append(CadEntity("LINE", "A-STAIR", {"start": (2300, y), "end": (3500, y)}))
    entities.extend(
        [
            CadEntity("LINE", "A-STAIR", {"start": (1000, 1000), "end": (1000, 2760)}),
            CadEntity("LINE", "A-STAIR", {"start": (3500, 1000), "end": (3500, 2760)}),
        ]
    )
    return entities


def stair_plan_with_platform_gap_entities() -> list[CadEntity]:
    entities = [CadEntity("TEXT", "A-TITLE", {"point": (0, 5000), "height": 300, "text": "stair plan"})]
    x = 1000
    y = 1000
    for _ in range(10):
        entities.append(CadEntity("LINE", "A-STAIR", {"start": (x, y), "end": (x + 220, y)}))
        entities.append(CadEntity("LINE", "A-STAIR", {"start": (x + 220, y), "end": (x + 220, y + 150)}))
        x += 220
        y += 150
    entities.append(CadEntity("LINE", "A-STAIR", {"start": (3200, 2500), "end": (4200, 2500)}))
    entities.append(CadEntity("LINE", "A-STAIR", {"start": (3200, 2600), "end": (4200, 2600)}))
    return entities


def stair_result_without_project_span() -> dict:
    return {
        "notes": {"drawing_type": "architectural_detail", "drawing_title": "\u697c\u68af\u8be6\u56fe", "text_items": []},
        "coordinate_system": {"origin": [0, 0]},
        "plan_summary": {"floor_heights": [], "elevation_marks": []},
        "axes": [],
        "walls": [],
        "wall_runs": [],
        "openings": [],
        "columns": [],
        "floors": [],
        "floor_openings": [],
        "stairs": [
            {
                "id": "ST001",
                "stair_type": "double_run_stair",
                "start_level": None,
                "end_level": None,
                "start": (1000, 1000),
                "end": (1000, 6000),
                "boundary_points": [(0, 0), (3000, 0), (3000, 6000), (0, 6000)],
                "stairwell_opening_boundary": [(0, 0), (3000, 0), (3000, 6000), (0, 6000)],
                "opening_required": True,
                "total_rise_mm": 3300,
                "total_run_mm": 7400,
                "width_mm": 1000,
                "stairwell_width_mm": 100,
                "run_count": 2,
                "risers_per_run": 11,
                "treads_per_run": 10,
                "run_length_mm": 2200,
                "landing_length_mm": 3000,
                "landing_width_mm": 1000,
                "riser_height_mm": 150,
                "tread_depth_mm": 220,
                "number_of_risers": 22,
                "number_of_treads": 20,
                "direction": "north",
                "source": "test",
                "source_segment_count": 88,
                "confidence": 0.92,
                "needs_review": False,
                "remarks": "",
            }
        ],
        "railings": [],
        "dimensions": [],
        "raw_geometry": [],
        "counts": {"stairs": 1},
        "input": {},
    }


def plan_result_for_floor(title: str, floor_height: int | None = None, with_floor: bool = False) -> dict:
    result = {
        "notes": {
            "drawing_type": "architectural_plan",
            "drawing_title": "\u5e73\u9762\u56fe",
            "drawing_title_candidates": [{"text": "\u5e73\u9762\u56fe", "raw_text": title}],
            "text_items": [{"text": title, "point": (0, 0), "layer": "A-TEXT", "height": 250, "rotation": 0}],
        },
        "coordinate_system": {"origin": [0, 0]},
        "plan_summary": {"floor_heights": [], "elevation_marks": []},
        "axes": [],
        "walls": [],
        "wall_runs": [],
        "openings": [],
        "columns": [],
        "floors": [],
        "floor_openings": [],
        "stairs": [],
        "railings": [],
        "dimensions": [],
        "raw_geometry": [],
        "counts": {},
        "input": {},
    }
    if floor_height is not None:
        result["plan_summary"]["floor_heights"] = [{"height_mm": floor_height, "raw_text": "test", "confidence": 0.9}]
    if with_floor:
        result["floors"] = [
            {
                "id": f"FLOOR-{title}",
                "floor_type": "default_floor_slab",
                "local_boundary_points": [[0, 0], [6000, 0], [6000, 6000], [0, 6000]],
                "area": 36000000,
                "thickness_mm": 120,
                "elevation_mm": 0,
                "source": "test_floor",
                "confidence": 0.9,
                "opening_ids": [],
                "opening_count": 0,
                "needs_review": False,
                "remarks": "",
            }
        ]
    return result


if __name__ == "__main__":
    unittest.main()
