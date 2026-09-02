from __future__ import annotations

import unittest

from cad_plan_demo.model_requirements import attach_opening_annotations, extract_marks


class ModelRequirementsTests(unittest.TestCase):
    def test_extract_marks_supports_cad_paragraph_prefix_door_and_window_codes(self) -> None:
        marks = extract_marks(
            [
                {"text": "\\PD0921", "point": (0.0, 0.0)},
                {"text": "\\PC1512", "point": (1000.0, 0.0)},
                {"text": "\\PTLM1821", "point": (2000.0, 0.0)},
            ]
        )

        self.assertEqual([mark["text"] for mark in marks], ["D0921", "C1512", "TLM1821"])
        self.assertEqual([mark["kind"] for mark in marks], ["door", "window", "door"])
        self.assertEqual(marks[0]["width_mm"], 900)
        self.assertEqual(marks[0]["height_mm"], 2100)
        self.assertEqual(marks[2]["width_mm"], 1800)
        self.assertEqual(marks[2]["height_mm"], 2100)

    def test_attach_opening_annotations_sets_door_mark_and_height(self) -> None:
        result = {
            "notes": {
                "text_items": [
                    {"text": "\\PD0921", "point": (100.0, 0.0)},
                ]
            },
            "openings": [
                {"id": "O0001", "kind": "door", "point": (0.0, 0.0), "width": 1000},
            ],
        }

        attach_opening_annotations(result)

        door = result["openings"][0]
        self.assertEqual(door["annotation"], "D0921")
        self.assertEqual(door["width"], 900)
        self.assertEqual(door["width_geometry_original"], 1000)
        self.assertEqual(door["width_source"], "nearest_text_annotation")
        self.assertEqual(door["height_mm"], 2100)
        self.assertEqual(door["sill_height_mm"], 0)

    def test_explicit_door_marks_reconcile_a_large_candidate_count_difference(self) -> None:
        result = {
            "notes": {
                "text_items": [
                    {"text": "D0721", "point": (float(index * 1000), 0.0)}
                    for index in range(5)
                ]
            },
            "walls": [{"id": "W0001", "start": [0.0, -100.0], "end": [5000.0, -100.0]}],
            "openings": [
                {"id": f"O{index:04d}", "kind": "door", "point": [float(index * 300), 0.0], "width": 700}
                for index in range(8)
            ],
        }

        attach_opening_annotations(result)

        doors = [opening for opening in result["openings"] if opening["kind"] == "door"]
        self.assertEqual(len(doors), 5)
        self.assertTrue(all(door["annotation"] == "D0721" for door in doors))
        self.assertTrue(all(door["annotation_source"] == "explicit_mark_count_reconciliation" for door in doors))

    def test_unmatched_explicit_door_mark_is_retained_for_review(self) -> None:
        result = {
            "notes": {
                "text_items": [
                    {"text": "D0921", "point": (float(index * 1000), 0.0)}
                    for index in range(8)
                ]
            },
            "walls": [{"id": "W0001", "start": [0.0, -100.0], "end": [5000.0, -100.0]}],
            "openings": [
                {"id": f"O{index:04d}", "kind": "door", "point": [float(index * 1000), 0.0], "width": 900}
                for index in range(5)
            ],
        }

        attach_opening_annotations(result)

        inferred = [door for door in result["openings"] if door.get("source") == "door_mark_without_geometry"]
        self.assertEqual(len(inferred), 3)
        self.assertTrue(all(door["needs_review"] for door in inferred))
        self.assertTrue(all(door["annotation"] == "D0921" for door in inferred))

    def test_inferred_door_uses_local_coordinates_and_opposite_corridor_walls(self) -> None:
        result = {
            "coordinate_system": {"origin": [10000.0, -5000.0]},
            "notes": {
                "text_items": [
                    {"text": "D1423", "point": (10000.0, 1200.0)},
                    {"text": "D1423", "point": (10020.0, 400.0)},
                    *[
                        {"text": "D0921", "point": (14000.0 + index * 700.0, 1200.0)}
                        for index in range(6)
                    ],
                ]
            },
            "walls": [
                {"id": "W-UPPER", "start": [10000.0, 1000.0], "end": [18000.0, 1000.0]},
                {"id": "W-LOWER", "start": [10000.0, -500.0], "end": [18000.0, -500.0]},
            ],
            "openings": [
                {"id": f"O{index:04d}", "kind": "door", "point": [14000.0 + index * 700.0, 1000.0], "width": 900}
                for index in range(4)
            ]
            + [
                {
                    "id": "O-MATCHED-D1423",
                    "kind": "door",
                    "point": [10500.0, 1000.0],
                    "width": 1400,
                    "host_wall_id": "W-UPPER",
                }
            ],
        }

        attach_opening_annotations(result)

        inferred = [door for door in result["openings"] if door.get("source") == "door_mark_without_geometry"]
        d1423 = [door for door in result["openings"] if door.get("annotation") == "D1423"]
        self.assertEqual({door["host_wall_id"] for door in d1423}, {"W-UPPER", "W-LOWER"})
        self.assertTrue(all("local_point" in door for door in inferred))
        self.assertTrue(all(door["local_point"][0] < door["point"][0] for door in inferred))
        inferred_d1423 = next(door for door in inferred if door.get("annotation") == "D1423")
        self.assertAlmostEqual(inferred_d1423["point"][0], 10520.0)


if __name__ == "__main__":
    unittest.main()
