from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from cad_plan_demo.dxf_parser import CadEntity
from cad_plan_demo.pipeline import analyze_entities
from cad_plan_demo.standard_export import write_standard_project_outputs


class ColumnRecognitionTests(unittest.TestCase):
    def test_rectangular_and_circular_columns_are_recognized(self) -> None:
        entities = [
            CadEntity(
                "LWPOLYLINE",
                "A-COLUMN",
                {"closed": True, "points": [(0, 0), (400, 0), (400, 300), (0, 300)]},
            ),
            CadEntity("CIRCLE", "A-COLUMN", {"center": (1200, 1000), "radius": 250}),
            CadEntity(
                "LWPOLYLINE",
                "A-WINDOW",
                {"closed": True, "points": [(2000, 0), (3200, 0), (3200, 120), (2000, 120)]},
            ),
        ]

        result = analyze_entities(entities, Path("synthetic_columns.dxf"), Path("synthetic_columns.dxf"))
        columns = result["columns"]

        self.assertEqual(result["counts"]["columns"], 2)
        self.assertEqual([column["column_type"] for column in columns], ["rectangular_column", "circular_column"])
        self.assertEqual(columns[0]["center"], (200.0, 150.0))
        self.assertEqual(columns[0]["width"], 400)
        self.assertEqual(columns[0]["depth"], 300)
        self.assertEqual(columns[1]["center"], (1200.0, 1000.0))
        self.assertEqual(columns[1]["diameter"], 500)
        self.assertNotIn("columns", result.get("openings", []))

    def test_standard_export_writes_columns_csv_rows(self) -> None:
        entities = [
            CadEntity(
                "LWPOLYLINE",
                "A-COLUMN",
                {"closed": True, "points": [(0, 0), (400, 0), (400, 400), (0, 400)]},
            )
        ]
        result = analyze_entities(entities, Path("synthetic_columns.dxf"), Path("synthetic_columns.dxf"))

        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(tmp, [("columns", result)], Path("synthetic_columns.dxf"))
            with (export.csv_dir / "Columns.csv").open("r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Column_Type"], "rectangular_column")
        self.assertEqual(rows[0]["Width"], "400.0")
        self.assertEqual(rows[0]["Depth"], "400.0")

    def test_rectangular_column_with_split_side_is_recognized(self) -> None:
        entities = [
            CadEntity("LINE", "A-COLUMN", {"start": (0, 0), "end": (300, 0)}),
            CadEntity("LINE", "A-COLUMN", {"start": (300, 0), "end": (300, 300)}),
            CadEntity("LINE", "A-COLUMN", {"start": (300, 300), "end": (0, 300)}),
            CadEntity("LINE", "A-COLUMN", {"start": (0, 0), "end": (0, 150)}),
            CadEntity("LINE", "A-COLUMN", {"start": (0, 150), "end": (0, 300)}),
        ]

        result = analyze_entities(entities, Path("synthetic_split_column.dxf"), Path("synthetic_split_column.dxf"))

        self.assertEqual(result["counts"]["columns"], 1)
        self.assertEqual(result["columns"][0]["column_type"], "rectangular_column")
        self.assertEqual(result["columns"][0]["width"], 300)
        self.assertEqual(result["columns"][0]["depth"], 300)
        self.assertEqual(result["columns"][0]["source"], "fragmented_line_rectangle")


if __name__ == "__main__":
    unittest.main()
