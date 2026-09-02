import unittest

from cad_plan_demo.bim.normalizer import _normalize_opening


class OpeningMarkPriorityTests(unittest.TestCase):
    def test_door_mark_overrides_low_confidence_elevation_height(self):
        levels = [{"id": "LEVEL-001", "name": "Level 1", "elevation_mm": 0.0}]
        row = {
            "id": "DOOR-004",
            "annotation": "D1421",
            "location_x": "1000",
            "location_y": "2000",
            "width_mm": "1400",
            "height_mm": "1800",
            "height_source": "matched_elevation_opening_needs_review",
            "host_wall_id": "WALL-014",
            "level": "Level 1",
            "mechanical_category": "double_swing_door",
        }

        door = _normalize_opening(row, 1, levels, {"Level 1": "Level 1"}, "door")

        self.assertEqual(1400.0, door["width_mm"])
        self.assertEqual(2100.0, door["height_mm"])
        self.assertEqual("opening_mark_override", door["height_source"])
        self.assertEqual("double_swing_door", door["semantic_type"])
        self.assertEqual("ready", door["review_status"])


if __name__ == "__main__":
    unittest.main()
