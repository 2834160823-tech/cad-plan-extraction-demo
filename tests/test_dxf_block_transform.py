from __future__ import annotations

import unittest

from cad_plan_demo.dxf_parser import _transform_block_point
from cad_plan_demo.recognizer import CadEntity, recognize


class DxfBlockTransformTests(unittest.TestCase):
    def test_mirrored_rotated_block_point_uses_full_insert_transform(self) -> None:
        point = _transform_block_point(
            (100.0, 200.0),
            {"point": (1000.0, 2000.0), "scale": (-1.0, 1.0), "rotation": 180.0},
            (0.0, 0.0),
        )

        self.assertAlmostEqual(point[0], 1100.0)
        self.assertAlmostEqual(point[1], 1800.0)

    def test_distant_block_door_is_not_forced_onto_unrelated_wall(self) -> None:
        entities = [
            CadEntity("LINE", "A-WALL", {"start": (0, 0), "end": (3000, 0)}),
            CadEntity("LINE", "A-WALL", {"start": (0, 200), "end": (3000, 200)}),
            CadEntity("INSERT", "A-DOOR", {"name": "D0921", "point": (1500, 5000)}),
        ]

        doors = [item for item in recognize(entities)["openings"] if item["kind"] == "door"]

        self.assertEqual(len(doors), 1)
        self.assertIsNone(doors[0]["host_wall_id"])


if __name__ == "__main__":
    unittest.main()
