from __future__ import annotations

import csv
from math import hypot
import tempfile
import unittest
from pathlib import Path

from cad_plan_demo.dxf_parser import CadEntity
from cad_plan_demo.pipeline import analyze_entities
from cad_plan_demo.railing_recognition import enrich_railings_with_section_height
from cad_plan_demo.standard_export import write_standard_project_outputs


class RailingRecognitionTests(unittest.TestCase):
    def test_paired_railing_lines_create_centerline_and_stairwell_distance(self) -> None:
        result = analyze_entities(railing_entities(), Path("synthetic_railing.dxf"), Path("synthetic_railing.dxf"))

        self.assertEqual(result["counts"]["railings"], 1)
        railing = result["railings"][0]
        self.assertEqual(railing["height_mm"], 1100)
        self.assertEqual(railing["start"], (1000.0, 1300.0))
        self.assertEqual(railing["end"], (3000.0, 1300.0))
        self.assertEqual(railing["distance_to_stairwell_mm"], 300)
        self.assertEqual(railing["source"], "paired_railing_lines")
        self.assertFalse(railing["needs_review"])

    def test_standard_export_writes_railing_rows(self) -> None:
        result = analyze_entities(railing_entities(), Path("synthetic_railing.dxf"), Path("synthetic_railing.dxf"))

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, [("railing", result)], Path("synthetic_railing.dxf"))
            with (export.csv_dir / "Railings.csv").open("r", encoding="utf-8-sig", newline="") as f:
                railings = list(csv.DictReader(f))

        self.assertEqual(len(railings), 1)
        self.assertEqual(railings[0]["Railing_Type"], "stair_railing")
        self.assertEqual(float(railings[0]["Height"]), 1100)
        self.assertEqual(float(railings[0]["Distance_To_Stairwell"]), 300)

    def test_stair_section_vertical_railing_line_supplies_height(self) -> None:
        result = analyze_entities(
            railing_section_entities(),
            Path("synthetic_railing_section.dxf"),
            Path("synthetic_railing_section.dxf"),
        )

        candidates = result["railing_height_candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["height_mm"], 1100)
        self.assertEqual(candidates[0]["source"], "section_vertical_railing_line")

    def test_plan_railing_height_is_filled_from_section(self) -> None:
        plan = analyze_entities(
            railing_plan_without_height_entities(),
            Path("synthetic_railing_plan.dxf"),
            Path("synthetic_railing_plan.dxf"),
        )
        section = analyze_entities(
            railing_section_entities(),
            Path("synthetic_railing_section.dxf"),
            Path("synthetic_railing_section.dxf"),
        )

        self.assertIsNone(plan["railings"][0]["height_mm"])
        filled = enrich_railings_with_section_height([("plan", plan), ("section", section)])

        self.assertEqual(filled, 1)
        self.assertEqual(plan["railings"][0]["height_mm"], 1100)
        self.assertIn("section/detail", plan["railings"][0]["remarks"])

    def test_long_railing_connected_to_vertical_returns_is_not_removed_as_duplicate(self) -> None:
        result = analyze_entities(
            long_u_shaped_railing_entities(),
            Path("synthetic_long_railing.dxf"),
            Path("synthetic_long_railing.dxf"),
        )

        self.assertEqual(result["counts"]["railings"], 3)
        lengths = sorted(
            hypot(
                railing["end"][0] - railing["start"][0],
                railing["end"][1] - railing["start"][1],
            )
            for railing in result["railings"]
        )
        self.assertGreater(lengths[-1], 20000)


def railing_entities() -> list[CadEntity]:
    return [
        CadEntity("TEXT", "A-TITLE", {"point": (0, 5000), "height": 300, "text": "stair plan"}),
        CadEntity("TEXT", "A-TEXT", {"point": (500, 4500), "height": 200, "text": "railing height 1100"}),
        CadEntity("LINE", "A-STAIR", {"start": (1000, 1000), "end": (3000, 1000)}),
        CadEntity("LINE", "A-STAIR", {"start": (3000, 1000), "end": (3000, 3000)}),
        CadEntity("LINE", "A-STAIR", {"start": (3000, 3000), "end": (1000, 3000)}),
        CadEntity("LINE", "A-STAIR", {"start": (1000, 3000), "end": (1000, 1000)}),
        CadEntity("LINE", "A-STAIR", {"start": (1100, 1200), "end": (2900, 1200)}),
        CadEntity("LINE", "A-STAIR", {"start": (1100, 1400), "end": (2900, 1400)}),
        CadEntity("LINE", "A-RAILING", {"start": (1000, 1250), "end": (3000, 1250)}),
        CadEntity("LINE", "A-RAILING", {"start": (1000, 1350), "end": (3000, 1350)}),
    ]


def railing_plan_without_height_entities() -> list[CadEntity]:
    return [
        CadEntity("TEXT", "A-TITLE", {"point": (0, 5000), "height": 300, "text": "stair plan"}),
        CadEntity("LINE", "A-STAIR", {"start": (1000, 1000), "end": (3000, 1000)}),
        CadEntity("LINE", "A-STAIR", {"start": (3000, 1000), "end": (3000, 3000)}),
        CadEntity("LINE", "A-STAIR", {"start": (3000, 3000), "end": (1000, 3000)}),
        CadEntity("LINE", "A-STAIR", {"start": (1000, 3000), "end": (1000, 1000)}),
        CadEntity("LINE", "A-RAILING", {"start": (1000, 1250), "end": (3000, 1250)}),
        CadEntity("LINE", "A-RAILING", {"start": (1000, 1350), "end": (3000, 1350)}),
    ]


def railing_section_entities() -> list[CadEntity]:
    return [
        CadEntity("TEXT", "A-TITLE", {"point": (0, 3000), "height": 300, "text": "stair section"}),
        CadEntity("LINE", "A-RAILING", {"start": (1000, 0), "end": (1000, 1100)}),
        CadEntity("LINE", "A-RAILING", {"start": (1200, 0), "end": (1200, 900)}),
        CadEntity("LINE", "A-RAILING", {"start": (800, 1100), "end": (1600, 1100)}),
    ]


def long_u_shaped_railing_entities() -> list[CadEntity]:
    return [
        CadEntity("LINE", "A-RAILING", {"start": (0, 0), "end": (0, 4300)}),
        CadEntity("LINE", "A-RAILING", {"start": (40, 0), "end": (40, 4300)}),
        CadEntity("LINE", "A-RAILING", {"start": (21600, 0), "end": (21600, 4300)}),
        CadEntity("LINE", "A-RAILING", {"start": (21560, 0), "end": (21560, 4300)}),
        CadEntity("LINE", "A-RAILING", {"start": (0, 0), "end": (21600, 0)}),
        CadEntity("LINE", "A-RAILING", {"start": (40, 40), "end": (21560, 40)}),
    ]


if __name__ == "__main__":
    unittest.main()
