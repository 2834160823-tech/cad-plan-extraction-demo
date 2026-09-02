from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from cad_plan_demo.dxf_parser import CadEntity
from cad_plan_demo.pipeline import add_stair_block_floor_openings, analyze_entities
from cad_plan_demo.standard_export import write_standard_project_outputs


class FloorOpeningRecognitionTests(unittest.TestCase):
    def test_floor_opening_rectangle_with_two_segment_foldline_is_recognized(self) -> None:
        entities = floor_opening_entities()

        result = analyze_entities(entities, Path("synthetic_floor_opening.dxf"), Path("synthetic_floor_opening.dxf"))

        self.assertEqual(result["counts"]["floor_openings"], 1)
        self.assertEqual(result["counts"]["floors"], 1)
        opening = result["floor_openings"][0]
        self.assertEqual(opening["opening_type"], "rectangular_floor_opening")
        self.assertEqual(opening["center"], (1500.0, 1000.0))
        self.assertEqual(opening["width"], 1000)
        self.assertEqual(opening["depth"], 800)
        self.assertEqual(opening["host_floor_id"], "FLOOR0001")
        self.assertEqual(opening["source"], "hole_layer_rectangle_with_foldline")
        self.assertIn("local_boundary_points", opening)

    def test_standard_export_writes_floor_and_floor_opening_rows(self) -> None:
        result = analyze_entities(floor_opening_entities(), Path("synthetic_floor_opening.dxf"), Path("synthetic_floor_opening.dxf"))

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, [("floor_opening", result)], Path("synthetic_floor_opening.dxf"))
            with (export.csv_dir / "Floors.csv").open("r", encoding="utf-8-sig", newline="") as f:
                floors = list(csv.DictReader(f))
            with (export.csv_dir / "Floor_Openings.csv").open("r", encoding="utf-8-sig", newline="") as f:
                openings = list(csv.DictReader(f))
            model = json.loads(export.ai_model.read_text(encoding="utf-8"))

        self.assertEqual(len(floors), 1)
        self.assertEqual(len(openings), 1)
        self.assertEqual(openings[0]["Host_Floor_ID"], "FLOOR-001")
        self.assertEqual(openings[0]["Opening_Type"], "rectangular_floor_opening")
        self.assertTrue(json.loads(openings[0]["Boundary_Points"]))
        self.assertEqual(model["summary"]["floors"], 1)
        self.assertEqual(model["summary"]["floor_openings"], 1)
        self.assertIn("floor_openings", model["drawings"][0]["elements"])

    def test_floor_opening_foldline_can_touch_rectangle_corners(self) -> None:
        entities = [
            CadEntity("LINE", "A-HOLE", {"start": (0, 0), "end": (1000, 0)}),
            CadEntity("LINE", "A-HOLE", {"start": (1000, 0), "end": (1000, 1000)}),
            CadEntity("LINE", "A-HOLE", {"start": (1000, 1000), "end": (0, 1000)}),
            CadEntity("LINE", "A-HOLE", {"start": (0, 1000), "end": (0, 0)}),
            CadEntity("LINE", "A-HOLE", {"start": (0, 0), "end": (600, 350)}),
            CadEntity("LINE", "A-HOLE", {"start": (600, 350), "end": (1000, 1000)}),
        ]

        result = analyze_entities(entities, Path("synthetic_corner_foldline.dxf"), Path("synthetic_corner_foldline.dxf"))

        self.assertEqual(result["counts"]["floor_openings"], 1)
        self.assertEqual(result["floor_openings"][0]["width"], 1000)

    def test_stair_block_opening_stays_separate_from_generic_void_and_walls(self) -> None:
        generic = {
            "id": "FOP0001",
            "opening_type": "rectangular_floor_opening",
            "center": (1825.0, 2325.0),
            "source": "hole_layer_rectangle_with_foldline",
        }
        wall = {"id": "WALL-PLATFORM", "start": (5350.0, 7900.0), "end": (7100.0, 7900.0)}
        result = {"floor_openings": [generic], "walls": [wall]}
        entities = [
            CadEntity(
                "INSERT",
                "楼梯",
                {
                    "name": "Stair-a",
                    "point": (5425.0, 7960.0),
                    "block_bounds": (3750.0, 6100.0, 7100.0, 9820.0),
                    "block_layers": ["楼梯", "栏杆扶手"],
                    "block_geometry_count": 68,
                },
            )
        ]

        add_stair_block_floor_openings(entities, result)

        self.assertEqual(2, len(result["floor_openings"]))
        stair_opening = result["floor_openings"][1]
        self.assertEqual("stairwell_opening", stair_opening["opening_type"])
        self.assertEqual("stair_block_bounds", stair_opening["source"])
        self.assertEqual((5425.0, 7960.0), stair_opening["center"])
        self.assertEqual([wall], result["walls"])


def floor_opening_entities() -> list[CadEntity]:
    return [
        CadEntity("LINE", "A-HOLE", {"start": (1000, 600), "end": (2000, 600)}),
        CadEntity("LINE", "A-HOLE", {"start": (2000, 600), "end": (2000, 1400)}),
        CadEntity("LINE", "A-HOLE", {"start": (2000, 1400), "end": (1000, 1400)}),
        CadEntity("LINE", "A-HOLE", {"start": (1000, 1400), "end": (1000, 600)}),
        CadEntity("LINE", "A-HOLE", {"start": (1200, 800), "end": (1500, 1200)}),
        CadEntity("LINE", "A-HOLE", {"start": (1500, 1200), "end": (1800, 800)}),
    ]


if __name__ == "__main__":
    unittest.main()
