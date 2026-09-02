from __future__ import annotations

import unittest

from cad_plan_demo.bim.normalizer import _assign_floor_plans
from cad_plan_demo.standard_export import infer_project_levels


class BilingualFloorPlanLevelMappingTests(unittest.TestCase):
    def test_british_plan_titles_create_ground_first_second_and_roof_levels(self) -> None:
        drawing_results = [
            ("frame-3", {"notes": {"drawing_title": "Ground Floor Plan"}}),
            ("frame-4", {"notes": {"drawing_title": "First Floor Plan"}}),
            ("frame-5", {"notes": {"drawing_title": "Second Floor Plan"}}),
            ("frame-6", {"notes": {"drawing_title": "Roof Floor Plan"}}),
        ]

        drawing_levels, levels = infer_project_levels("PROJECT-001", drawing_results, 3600.0)

        self.assertEqual([0.0, 3600.0, 7200.0, 10800.0], [row["Elevation"] for row in levels])
        self.assertEqual(
            ["LEVEL-001", "LEVEL-002", "LEVEL-003", "LEVEL-004"],
            [drawing_levels[name] for name, _ in drawing_results],
        )

    def test_explicit_level_id_overrides_a_wrong_english_title(self) -> None:
        floors = [
            {
                "floor_number": 1,
                "floor_name": "1F",
                "base_level_id": "LEVEL-001",
                "base_level_name": "Level 1",
                "top_level_name": "Level 2",
                "floor_height_mm": 3600.0,
            },
            {
                "floor_number": 2,
                "floor_name": "2F",
                "base_level_id": "LEVEL-002",
                "base_level_name": "Level 2",
                "top_level_name": "Level 3",
                "floor_height_mm": 3600.0,
            },
        ]
        drawings = [
            {
                "drawing_id": "DRAWING-004",
                "drawing_name": "@Arial Unicode MSRoof Floor Plan",
                "drawing_number": "4",
                "drawing_type": "floor_plan",
                "level_id": "LEVEL-002",
            }
        ]

        assignment = _assign_floor_plans(drawings, floors)[0]

        self.assertEqual("Level 2", assignment["assigned_base_level"])
        self.assertEqual(0.98, assignment["confidence"])
        self.assertEqual("ready", assignment["review_status"])
        self.assertEqual("Matched from explicit Drawings.csv Level_ID.", assignment["reason"])


if __name__ == "__main__":
    unittest.main()
