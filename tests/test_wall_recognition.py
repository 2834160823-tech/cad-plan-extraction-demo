from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from cad_plan_demo.dxf_parser import CadEntity
from cad_plan_demo.pipeline import analyze_entities
from cad_plan_demo.standard_export import write_standard_project_outputs
from cad_plan_demo.wall_runs import make_wall_runs


class WallRecognitionTests(unittest.TestCase):
    def test_door_hinge_point_bridges_wall_gap_when_center_is_off_axis(self) -> None:
        walls = [
            {
                "id": "W-PIER",
                "start": [0.0, 0.0],
                "end": [100.0, 0.0],
                "local_start": [0.0, 0.0],
                "local_end": [100.0, 0.0],
                "normalized_width": 200.0,
                "confidence": 0.95,
            },
            {
                "id": "W-RIGHT",
                "start": [1000.0, 0.0],
                "end": [3000.0, 0.0],
                "local_start": [1000.0, 0.0],
                "local_end": [3000.0, 0.0],
                "normalized_width": 200.0,
                "confidence": 0.95,
            },
        ]
        openings = [
            {
                "id": "D-1",
                "point": [100.0, -450.0],
                "panel_start": [100.0, 0.0],
                "panel_end": [100.0, -900.0],
                "width": 900.0,
                "host_wall_id": "W-PIER",
            }
        ]

        runs = make_wall_runs(walls, openings)

        self.assertEqual(1, len(runs))
        self.assertEqual([0.0, 0.0], runs[0]["local_start"])
        self.assertEqual([3000.0, 0.0], runs[0]["local_end"])
        self.assertEqual(["W-PIER", "W-RIGHT"], runs[0]["source_wall_ids"])

    def test_door_gap_does_not_merge_different_wall_thicknesses(self) -> None:
        walls = [
            {"id": "W-100", "start": [0, 0], "end": [100, 0], "normalized_width": 100},
            {"id": "W-200", "start": [1000, 0], "end": [3000, 0], "normalized_width": 200},
        ]
        openings = [
            {
                "id": "D-1",
                "point": [100, -450],
                "panel_start": [100, 0],
                "panel_end": [100, -900],
                "width": 900,
                "host_wall_id": "W-100",
            }
        ]

        runs = make_wall_runs(walls, openings)

        self.assertEqual(2, len(runs))

    def test_dedicated_parapet_layer_is_not_exported_as_wall(self) -> None:
        entities = [
            CadEntity("TEXT", "A-TITLE", {"point": (0, 3500), "height": 250, "text": "屋顶平面图"}),
            CadEntity("LINE", "A-PARAPET", {"start": (0, 0), "end": (3000, 0)}),
            CadEntity("LINE", "A-PARAPET", {"start": (0, 200), "end": (3000, 200)}),
            CadEntity("LINE", "A-PARAPET", {"start": (0, 3000), "end": (3000, 3000)}),
            CadEntity("LINE", "A-PARAPET", {"start": (0, 3200), "end": (3000, 3200)}),
            CadEntity("LINE", "A-PARAPET", {"start": (0, 0), "end": (0, 3000)}),
            CadEntity("LINE", "A-PARAPET", {"start": (200, 0), "end": (200, 3000)}),
            CadEntity("LINE", "A-PARAPET", {"start": (3000, 0), "end": (3000, 3000)}),
            CadEntity("LINE", "A-PARAPET", {"start": (3200, 0), "end": (3200, 3000)}),
        ]
        source = Path("synthetic_dedicated_parapet.dxf")
        result = analyze_entities(entities, source, source)

        self.assertEqual(result["counts"]["walls"], 0)
        self.assertEqual(result["counts"]["parapets"], 4)
        self.assertEqual(result["counts"]["floors"], 1)

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, [("roof", result)], source)
            with (export.csv_dir / "Walls.csv").open("r", encoding="utf-8-sig", newline="") as f:
                walls = list(csv.DictReader(f))
            with (export.csv_dir / "Parapets.csv").open("r", encoding="utf-8-sig", newline="") as f:
                parapets = list(csv.DictReader(f))

        self.assertEqual(walls, [])
        self.assertEqual(len(parapets), 4)
        self.assertTrue(all(row["Parapet_Type"] == "dedicated_layer_parapet" for row in parapets))
        self.assertTrue(all(row["Host_Roof_ID"] == "ROOF-001" for row in parapets))
        self.assertTrue(all(row["Thickness"] == "200" for row in parapets))

    def test_duplicate_wall_lines_do_not_create_duplicate_walls(self) -> None:
        entities = [
            CadEntity("LINE", "Wall", {"start": (0, 0), "end": (3000, 0)}),
            CadEntity("LINE", "Wall", {"start": (0, 300), "end": (3000, 300)}),
            CadEntity("LINE", "Wall", {"start": (0, 300), "end": (3000, 300)}),
        ]

        result = analyze_entities(entities, Path("synthetic_duplicate_wall.dxf"), Path("synthetic_duplicate_wall.dxf"))

        self.assertEqual(result["counts"]["walls"], 1)
        self.assertEqual(result["walls"][0]["start"], (0.0, 150.0))
        self.assertEqual(result["walls"][0]["end"], (3000.0, 150.0))

    def test_small_square_wall_block_keeps_surrounding_wall_direction(self) -> None:
        entities = [
            CadEntity("LINE", "Wall", {"start": (0, 0), "end": (1000, 0)}),
            CadEntity("LINE", "Wall", {"start": (0, 300), "end": (1000, 300)}),
            CadEntity("LINE", "Wall", {"start": (1300, 0), "end": (2300, 0)}),
            CadEntity("LINE", "Wall", {"start": (1300, 300), "end": (2300, 300)}),
            CadEntity("LINE", "Wall", {"start": (1000, 0), "end": (1000, 300)}),
            CadEntity("LINE", "Wall", {"start": (1300, 0), "end": (1300, 300)}),
            CadEntity("LINE", "Wall", {"start": (1000, 0), "end": (1300, 0)}),
            CadEntity("LINE", "Wall", {"start": (1000, 300), "end": (1300, 300)}),
        ]

        result = analyze_entities(
            entities,
            Path("synthetic_square_wall_block.dxf"),
            Path("synthetic_square_wall_block.dxf"),
        )
        walls = result["walls"]

        self.assertEqual(result["counts"]["walls"], 3)
        self.assertIn(
            ((1000.0, 150.0), (1300.0, 150.0)),
            [(wall["start"], wall["end"]) for wall in walls],
        )
        self.assertNotIn(
            ((1150.0, 0.0), (1150.0, 300.0)),
            [(wall["start"], wall["end"]) for wall in walls],
        )

    def test_wall_run_groups_segments_across_window_opening(self) -> None:
        entities = [
            CadEntity("LINE", "Wall", {"start": (0, 0), "end": (1000, 0)}),
            CadEntity("LINE", "Wall", {"start": (0, 300), "end": (1000, 300)}),
            CadEntity("LINE", "Wall", {"start": (2200, 0), "end": (4000, 0)}),
            CadEntity("LINE", "Wall", {"start": (2200, 300), "end": (4000, 300)}),
            CadEntity(
                "LWPOLYLINE",
                "A-WINDOW",
                {"closed": True, "points": [(1000, 55), (2200, 55), (2200, 145), (1000, 145)]},
            ),
        ]

        result = analyze_entities(
            entities,
            Path("synthetic_wall_run_with_window.dxf"),
            Path("synthetic_wall_run_with_window.dxf"),
        )

        self.assertEqual(result["counts"]["walls"], 2)
        self.assertEqual(result["counts"]["wall_runs"], 1)
        run = result["wall_runs"][0]
        self.assertEqual(run["local_start"], [0.0, 150.0])
        self.assertEqual(run["local_end"], [4000.0, 150.0])
        self.assertEqual(run["source_wall_count"], 2)
        self.assertEqual(run["opening_count"], 1)
        self.assertEqual(result["openings"][0]["host_wall_run_id"], run["id"])


if __name__ == "__main__":
    unittest.main()
