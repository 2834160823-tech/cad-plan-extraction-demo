from __future__ import annotations

import unittest

from cad_plan_demo.dxf_parser import CadEntity
from cad_plan_demo.floor_slabs import build_default_floor_slabs


class FloorSlabTests(unittest.TestCase):
    def test_dedicated_balcony_layer_creates_separate_slab_without_length_limit(self) -> None:
        result = {
            "notes": {"drawing_type": "architectural_plan"},
            "coordinate_system": {"origin": [1000.0, 2000.0]},
            "walls": [
                {"local_start": [0.0, 4500.0], "local_end": [21600.0, 4500.0]},
                {"local_start": [0.0, 10500.0], "local_end": [21600.0, 10500.0]},
            ],
            "floor_openings": [],
        }
        entities = [
            CadEntity("LINE", "阳台板", {"start": (1000.0, 2000.0), "end": (22900.0, 2000.0)}),
            CadEntity("LINE", "阳台板", {"start": (22900.0, 2000.0), "end": (22900.0, 6500.0)}),
            CadEntity("LINE", "阳台板", {"start": (22900.0, 6500.0), "end": (1000.0, 6500.0)}),
            CadEntity("LINE", "阳台板", {"start": (1000.0, 6500.0), "end": (1000.0, 2000.0)}),
        ]

        build_default_floor_slabs(result, entities)

        self.assertEqual(result["counts"]["floors"], 2)
        balcony = next(floor for floor in result["floors"] if floor["floor_type"] == "balcony_slab")
        self.assertEqual(
            balcony["local_boundary_points"],
            [[0.0, 0.0], [21900.0, 0.0], [21900.0, 4500.0], [0.0, 4500.0]],
        )
        self.assertEqual(balcony["area"], 98550000.0)
        self.assertEqual(balcony["source"], "dedicated_balcony_slab_layer")
        self.assertIsNone(balcony["thickness_mm"])
        self.assertFalse(balcony["needs_review"])


if __name__ == "__main__":
    unittest.main()
