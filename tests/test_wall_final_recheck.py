from __future__ import annotations

import unittest

from cad_plan_demo.bim.normalizer import _dedupe_same_position_walls, _remap_wall_references


class WallFinalRecheckTests(unittest.TestCase):
    def test_reversed_and_contained_wall_is_removed_and_door_host_is_remapped(self) -> None:
        walls = [
            {
                "id": "W-LONG",
                "level": "Level 2",
                "start": {"x": 0, "y": 1000},
                "end": {"x": 5000, "y": 1000},
                "thickness_mm": 200,
                "material": "brick",
                "height_mm": 3000,
                "confidence": 0.95,
            },
            {
                "id": "W-DUP",
                "level": "Level 2",
                "start": {"x": 4500, "y": 1000},
                "end": {"x": 500, "y": 1000},
                "thickness_mm": 100,
                "material": "block",
                "height_mm": 3000,
                "confidence": 0.8,
            },
        ]
        report = []
        model = {
            "components": {
                "walls": walls,
                "doors": [{"id": "D-1", "host_wall_id": "W-DUP"}],
                "rooms": [{"id": "R-1", "adjacent_wall_ids": ["W-LONG", "W-DUP"]}],
            }
        }

        kept, replacements = _dedupe_same_position_walls(walls, report)
        model["components"]["walls"] = kept
        _remap_wall_references(model, replacements)

        self.assertEqual(["W-LONG"], [wall["id"] for wall in kept])
        self.assertEqual({"W-DUP": "W-LONG"}, replacements)
        self.assertEqual("W-LONG", model["components"]["doors"][0]["host_wall_id"])
        self.assertEqual(["W-LONG"], model["components"]["rooms"][0]["adjacent_wall_ids"])
        self.assertIn("thickness", report[0]["reason"])
        self.assertIn("material", report[0]["reason"])

    def test_same_coordinates_on_different_levels_are_not_duplicates(self) -> None:
        walls = [
            {"id": "W-L1", "level": "Level 1", "start": {"x": 0, "y": 0}, "end": {"x": 3000, "y": 0}},
            {"id": "W-L2", "level": "Level 2", "start": {"x": 0, "y": 0}, "end": {"x": 3000, "y": 0}},
        ]

        kept, replacements = _dedupe_same_position_walls(walls, [])

        self.assertEqual(2, len(kept))
        self.assertEqual({}, replacements)

    def test_cross_drawing_overlap_with_uncertain_floor_is_blocked_not_deleted(self) -> None:
        walls = [
            {"id": "W-A", "drawing_id": "PLAN-A", "base_level": "Level 1", "start": {"x": 0, "y": 0}, "end": {"x": 3000, "y": 0}},
            {"id": "W-B", "drawing_id": "PLAN-B", "base_level": "Level 1", "start": {"x": 0, "y": 0}, "end": {"x": 3000, "y": 0}},
        ]

        kept, replacements = _dedupe_same_position_walls(walls, [])

        self.assertEqual(2, len(kept))
        self.assertEqual({}, replacements)
        self.assertTrue(all(wall["modeling_status"] == "blocked" for wall in kept))
        self.assertTrue(all(wall["wall_recheck_status"] == "blocked_ambiguous_cross_drawing_overlap" for wall in kept))


if __name__ == "__main__":
    unittest.main()
