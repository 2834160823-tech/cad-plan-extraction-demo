from __future__ import annotations

import unittest

from cad_plan_demo.cross_view import apply_cross_view_opening_enrichment


class CrossViewEnrichmentTests(unittest.TestCase):
    def test_matched_elevation_window_backfills_plan_sill_height(self) -> None:
        plan_window = {
            "id": "P-WIN-001",
            "kind": "window",
            "width": 1200,
            "component_category": "unknown",
        }
        elevation_window = {
            "id": "E-WIN-001",
            "kind": "window",
            "width": 1200,
            "height_mm": 1500,
            "sill_height_mm": 900,
            "component_category": "casement_window",
        }
        drawing_results = [
            ("plan", {"openings": [plan_window]}),
            ("east_elevation", {"openings": [elevation_window]}),
        ]
        matches = [
            {
                "opening_kind": "window",
                "match_status": "matched",
                "plan_drawing": "plan",
                "plan_opening_id": "P-WIN-001",
                "elevation_drawing": "east_elevation",
                "elevation_opening_id": "E-WIN-001",
                "score": 0.92,
                "reason": "same annotation; similar width",
            }
        ]

        enriched = apply_cross_view_opening_enrichment(drawing_results, matches)

        self.assertEqual(enriched, 1)
        self.assertEqual(plan_window["sill_height_mm"], 900)
        self.assertEqual(plan_window["sill_height_source"], "matched_elevation_opening")
        self.assertEqual(plan_window["height_mm"], 1500)
        self.assertEqual(plan_window["height_source"], "matched_elevation_opening")
        self.assertEqual(plan_window["component_category"], "casement_window")
        self.assertEqual(plan_window["matched_elevation_drawing"], "east_elevation")
        self.assertEqual(plan_window["matched_elevation_opening_id"], "E-WIN-001")
        self.assertEqual(plan_window["cross_view_match_score"], 0.92)

    def test_needs_review_match_does_not_backfill_plan_window(self) -> None:
        plan_window = {"id": "P-WIN-001", "kind": "window"}
        elevation_window = {"id": "E-WIN-001", "kind": "window", "sill_height_mm": 900}
        drawing_results = [
            ("plan", {"openings": [plan_window]}),
            ("east_elevation", {"openings": [elevation_window]}),
        ]
        matches = [
            {
                "opening_kind": "window",
                "match_status": "needs_review",
                "plan_drawing": "plan",
                "plan_opening_id": "P-WIN-001",
                "elevation_drawing": "east_elevation",
                "elevation_opening_id": "E-WIN-001",
            }
        ]

        enriched = apply_cross_view_opening_enrichment(drawing_results, matches)

        self.assertEqual(enriched, 0)
        self.assertNotIn("sill_height_mm", plan_window)

    def test_needs_review_elevation_door_backfills_height_with_review_source(self) -> None:
        plan_door = {"id": "P-DOOR-001", "kind": "door", "width": 900}
        elevation_door = {"id": "E-DOOR-001", "kind": "door", "width": 900, "height_mm": 2100, "annotation": "M0921"}
        drawing_results = [
            ("plan", {"openings": [plan_door]}),
            ("south_elevation", {"openings": [elevation_door]}),
        ]
        matches = [
            {
                "opening_kind": "door",
                "match_status": "needs_review",
                "plan_drawing": "plan",
                "plan_opening_id": "P-DOOR-001",
                "elevation_drawing": "south_elevation",
                "elevation_opening_id": "E-DOOR-001",
                "score": 0.52,
                "reason": "same kind; similar width",
            }
        ]

        enriched = apply_cross_view_opening_enrichment(drawing_results, matches)

        self.assertEqual(enriched, 1)
        self.assertEqual(plan_door["height_mm"], 2100)
        self.assertEqual(plan_door["height_source"], "matched_elevation_opening_needs_review")
        self.assertEqual(plan_door["annotation"], "M0921")
        self.assertEqual(plan_door["cross_view_match_status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
