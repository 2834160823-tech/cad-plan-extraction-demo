from __future__ import annotations

import unittest
from pathlib import Path

from cad_plan_demo.dxf_parser import CadEntity
from cad_plan_demo.dxf_parser import parse_dxf
from cad_plan_demo.elevation_openings import elevation_component_category, elevation_line_rectangles, suppress_nested_elevation_rectangles
from cad_plan_demo.pipeline import analyze_entities
from cad_plan_demo.recognizer import (
    Opening,
    Wall,
    extract_segments,
    merge_adjacent_door_frames,
    recognize,
    recognize_door_arcs,
)


LEGEND_ROOT = Path("C:/Users/BIN/Desktop/Dissertation/Test3")


def legend_path(file_name: str) -> Path:
    direct = LEGEND_ROOT / file_name
    if direct.exists():
        return direct
    matches = list(LEGEND_ROOT.rglob(file_name))
    if matches:
        return matches[0]
    raise FileNotFoundError(direct)


class OpeningRecognitionTests(unittest.TestCase):
    def test_thick_panel_selects_perpendicular_host_at_wall_intersection(self) -> None:
        entities = [
            CadEntity("ARC", "A-DOOR", {"center": (0, 0), "radius": 900, "start_angle": 0, "end_angle": 90}),
            CadEntity("LINE", "A-DOOR", {"start": (0, 0), "end": (900, 0)}),
            CadEntity("LINE", "A-DOOR", {"start": (0, 20), "end": (900, 20)}),
        ]
        walls = [
            Wall("W-H", (-2000, 0), (2000, 0), 4000, 200, 200, "standard", "paired_wall_lines", ["A-WALL"], 0.95),
            Wall("W-V", (0, -2000), (0, 2000), 4000, 200, 200, "standard", "paired_wall_lines", ["A-WALL"], 0.95),
        ]
        openings = []

        recognize_door_arcs(entities, walls, openings)

        self.assertEqual(len(openings), 1)
        self.assertEqual(openings[0].host_wall_id, "W-V")
        self.assertEqual(openings[0].open_direction, "east")
        self.assertEqual(openings[0].point, (0.0, 450.0))

    def test_rectangle_window_and_parallel_door_are_recognized_once(self) -> None:
        entities = [
            CadEntity("LINE", "A-WALL", {"start": (0, 0), "end": (6000, 0)}),
            CadEntity("LINE", "A-WALL", {"start": (0, 200), "end": (6000, 200)}),
            CadEntity(
                "LWPOLYLINE",
                "A-WINDOW",
                {"closed": True, "points": [(1000, 55), (2200, 55), (2200, 145), (1000, 145)]},
            ),
            CadEntity("LINE", "A-DOOR", {"start": (3200, 65), "end": (4400, 65)}),
            CadEntity("LINE", "A-DOOR", {"start": (3200, 100), "end": (4400, 100)}),
            CadEntity("LINE", "A-DOOR", {"start": (3200, 135), "end": (4400, 135)}),
        ]

        result = analyze_entities(entities, Path("synthetic_openings.dxf"), Path("synthetic_openings.dxf"))
        doors = [opening for opening in result["openings"] if opening["kind"] == "door"]
        windows = [opening for opening in result["openings"] if opening["kind"] == "window"]

        self.assertEqual(len(doors), 1)
        self.assertEqual(len(windows), 1)
        self.assertEqual(doors[0]["source"], "parallel_door_lines")
        self.assertEqual(windows[0]["source"], "layer_closed_rectangle")
        self.assertEqual(doors[0]["width"], 1200)
        self.assertEqual(windows[0]["width"], 1200)

    def test_swing_arc_door_point_is_opening_center_not_hinge(self) -> None:
        entities = [
            CadEntity("LINE", "A-WALL", {"start": (0, 0), "end": (3000, 0)}),
            CadEntity("LINE", "A-WALL", {"start": (0, 300), "end": (3000, 300)}),
            CadEntity(
                "ARC",
                "A-DOOR",
                {"center": (0, 150), "radius": 900, "start_angle": 0, "end_angle": 90},
            ),
        ]

        result = analyze_entities(entities, Path("synthetic_arc_door.dxf"), Path("synthetic_arc_door.dxf"))
        doors = [opening for opening in result["openings"] if opening["kind"] == "door"]

        self.assertEqual(len(doors), 1)
        self.assertEqual(doors[0]["source"], "quarter_arc")
        self.assertEqual(doors[0]["point"], (450.0, 150.0))
        self.assertNotEqual(doors[0]["point"], (0.0, 150.0))
        self.assertEqual(doors[0]["swing_side"], "left")

    def test_swing_arc_suppresses_same_door_leaf_line(self) -> None:
        entities = [
            CadEntity("LINE", "A-WALL", {"start": (0, 0), "end": (3000, 0)}),
            CadEntity("LINE", "A-WALL", {"start": (0, 300), "end": (3000, 300)}),
            CadEntity(
                "ARC",
                "A-DOOR",
                {"center": (0, 150), "radius": 900, "start_angle": 0, "end_angle": 90},
            ),
            CadEntity("LINE", "A-DOOR", {"start": (0, -450), "end": (900, -450)}),
        ]

        result = analyze_entities(entities, Path("synthetic_arc_door_with_leaf.dxf"), Path("synthetic_arc_door_with_leaf.dxf"))
        doors = [opening for opening in result["openings"] if opening["kind"] == "door"]

        self.assertEqual(len(doors), 1)
        self.assertEqual(doors[0]["source"], "quarter_arc")
        self.assertEqual(doors[0]["point"], (450.0, 150.0))

    def test_swing_arc_records_right_swing_side(self) -> None:
        entities = [
            CadEntity("LINE", "A-WALL", {"start": (0, 0), "end": (3000, 0)}),
            CadEntity("LINE", "A-WALL", {"start": (0, 300), "end": (3000, 300)}),
            CadEntity(
                "ARC",
                "A-DOOR",
                {"center": (900, 150), "radius": 900, "start_angle": 90, "end_angle": 180},
            ),
        ]

        result = analyze_entities(entities, Path("synthetic_left_swing_door.dxf"), Path("synthetic_left_swing_door.dxf"))
        doors = [opening for opening in result["openings"] if opening["kind"] == "door"]

        self.assertEqual(len(doors), 1)
        self.assertEqual(doors[0]["point"], (450.0, 150.0))
        self.assertEqual(doors[0]["swing_side"], "right")

    def test_thick_door_panel_controls_swing_direction_perpendicular_to_host_wall(self) -> None:
        entities = [
            CadEntity("LINE", "A-WALL", {"start": (0, 0), "end": (3000, 0)}),
            CadEntity("LINE", "A-WALL", {"start": (0, 300), "end": (3000, 300)}),
            CadEntity(
                "ARC",
                "A-DOOR",
                {"center": (0, 150), "radius": 900, "start_angle": 0, "end_angle": 90},
            ),
            CadEntity("LINE", "A-DOOR", {"start": (0, 150), "end": (0, 1050)}),
            CadEntity("LINE", "A-DOOR", {"start": (20, 150), "end": (20, 1050)}),
        ]

        result = analyze_entities(entities, Path("synthetic_thick_panel_door.dxf"), Path("synthetic_thick_panel_door.dxf"))
        doors = [opening for opening in result["openings"] if opening["kind"] == "door"]

        self.assertEqual(1, len(doors))
        self.assertEqual("quarter_arc", doors[0]["source"])
        self.assertEqual("north", doors[0]["open_direction"])
        self.assertEqual("cad_door_panel_geometry", doors[0]["swing_source"])
        self.assertAlmostEqual(90.0, doors[0]["panel_wall_angle_deg"], delta=1.0)
        self.assertAlmostEqual(20.0, doors[0]["panel_thickness_mm"], places=3)
        self.assertEqual((0.0, 150.0), doors[0]["panel_start"])
        self.assertEqual((10.0, 1050.0), doors[0]["panel_end"])

    def test_elevation_door_rectangle_replaces_plan_line_openings(self) -> None:
        entities = [
            CadEntity("TEXT", "A-TEXT", {"point": (0, 3600), "text": "南立面图"}),
            CadEntity("LINE", "A-WALL", {"start": (0, 0), "end": (6300, 0)}),
            CadEntity("LINE", "A-WALL", {"start": (6300, 0), "end": (6300, 3300)}),
            CadEntity("LINE", "A-WALL", {"start": (6300, 3300), "end": (0, 3300)}),
            CadEntity("LINE", "A-WALL", {"start": (0, 3300), "end": (0, 0)}),
            CadEntity("LINE", "A-DOOR", {"start": (2500, 0), "end": (3400, 0)}),
            CadEntity("LINE", "A-DOOR", {"start": (3400, 0), "end": (3400, 2100)}),
            CadEntity("LINE", "A-DOOR", {"start": (3400, 2100), "end": (2500, 2100)}),
            CadEntity("LINE", "A-DOOR", {"start": (2500, 2100), "end": (2500, 0)}),
        ]

        result = analyze_entities(entities, Path("synthetic_elevation_door.dxf"), Path("synthetic_elevation_door.dxf"))

        self.assertEqual(result["notes"]["drawing_type"], "architectural_elevation")
        self.assertEqual(len(result["openings"]), 1)
        self.assertEqual(result["openings"][0]["kind"], "door")
        self.assertEqual(result["openings"][0]["source"], "elevation_rectangle")
        self.assertEqual(result["openings"][0]["width"], 900)
        self.assertEqual(result["openings"][0]["height_mm"], 2100)

    def test_elevation_mixed_layer_window_with_inner_lines_is_recognized_once(self) -> None:
        entities = [
            CadEntity("TEXT", "A-TEXT", {"point": (0, 3600), "text": "北立面图"}),
            CadEntity("LINE", "A-WALL", {"start": (0, 0), "end": (6300, 0)}),
            CadEntity("LINE", "A-WALL", {"start": (6300, 0), "end": (6300, 3300)}),
            CadEntity("LINE", "A-WALL", {"start": (6300, 3300), "end": (0, 3300)}),
            CadEntity("LINE", "A-WALL", {"start": (0, 3300), "end": (0, 0)}),
            CadEntity("LINE", "A-DOOR", {"start": (1000, 900), "end": (2200, 900)}),
            CadEntity("LINE", "A-WINDOW", {"start": (2200, 900), "end": (2200, 2400)}),
            CadEntity("LINE", "A-DOOR", {"start": (2200, 2400), "end": (1000, 2400)}),
            CadEntity("LINE", "A-WINDOW", {"start": (1000, 2400), "end": (1000, 900)}),
            CadEntity("LINE", "A-WINDOW", {"start": (1600, 900), "end": (1600, 2400)}),
            CadEntity("LINE", "A-WINDOW", {"start": (1000, 1650), "end": (2200, 1650)}),
        ]

        result = analyze_entities(entities, Path("synthetic_elevation_window.dxf"), Path("synthetic_elevation_window.dxf"))

        self.assertEqual(result["notes"]["drawing_type"], "architectural_elevation")
        self.assertEqual(len(result["openings"]), 1)
        self.assertEqual(result["openings"][0]["kind"], "window")
        self.assertEqual(result["openings"][0]["source"], "elevation_rectangle")
        self.assertEqual(result["openings"][0]["width"], 1200)
        self.assertEqual(result["openings"][0]["height_mm"], 1500)
        self.assertEqual(result["openings"][0]["sill_height_mm"], 900)

    def test_elevation_window_insert_block_bounds_are_recognized(self) -> None:
        entities = [
            CadEntity("TEXT", "A-TEXT", {"point": (0, 3600), "text": "北立面图"}),
            CadEntity("LINE", "A-WALL", {"start": (0, 0), "end": (6300, 0)}),
            CadEntity("LINE", "A-WALL", {"start": (6300, 0), "end": (6300, 3300)}),
            CadEntity("LINE", "A-WALL", {"start": (6300, 3300), "end": (0, 3300)}),
            CadEntity("LINE", "A-WALL", {"start": (0, 3300), "end": (0, 0)}),
            CadEntity(
                "INSERT",
                "洞口",
                {
                    "name": "pkc2",
                    "point": (1600, 1500),
                    "block_bounds": (1000, 900, 2200, 2100),
                    "block_layers": ["Window"],
                },
            ),
        ]

        result = analyze_entities(entities, Path("synthetic_elevation_window_insert.dxf"), Path("synthetic_elevation_window_insert.dxf"))

        self.assertEqual(result["notes"]["drawing_type"], "architectural_elevation")
        self.assertEqual(len(result["openings"]), 1)
        self.assertEqual(result["openings"][0]["kind"], "window")
        self.assertEqual(result["openings"][0]["source"], "elevation_block_bounds")
        self.assertEqual(result["openings"][0]["component_category"], "casement_window")
        self.assertEqual(result["openings"][0]["width"], 1200)
        self.assertEqual(result["openings"][0]["height_mm"], 1200)
        self.assertEqual(result["openings"][0]["sill_height_mm"], 900)

    def test_elevation_window_insert_block_bounds_respect_local_offsets(self) -> None:
        entities = [
            CadEntity("TEXT", "A-TEXT", {"point": (0, 7000), "text": "北立面图"}),
            CadEntity("LINE", "A-WALL", {"start": (0, 0), "end": (6300, 0)}),
            CadEntity("LINE", "A-WALL", {"start": (6300, 0), "end": (6300, 6600)}),
            CadEntity("LINE", "A-WALL", {"start": (6300, 6600), "end": (0, 6600)}),
            CadEntity("LINE", "A-WALL", {"start": (0, 6600), "end": (0, 0)}),
            CadEntity(
                "INSERT",
                "洞口",
                {
                    "name": "pkc2",
                    "point": (1600, 1500),
                    "block_bounds": (1000, 900, 2200, 2100),
                    "block_layers": ["Window"],
                },
            ),
            CadEntity(
                "INSERT",
                "洞口",
                {
                    "name": "pkc2",
                    "point": (1600, 4800),
                    "block_bounds": (1000, 4200, 2200, 5400),
                    "block_layers": ["Window"],
                },
            ),
        ]

        result = analyze_entities(entities, Path("synthetic_elevation_window_insert_offsets.dxf"), Path("synthetic_elevation_window_insert_offsets.dxf"))

        self.assertEqual(len(result["openings"]), 2)
        self.assertEqual([opening["sill_height_mm"] for opening in result["openings"]], [900, 4200])

    def test_window_legend_categories_from_elevation_symbols(self) -> None:
        cases = {
            "单扇平开窗.dxf": "casement_window",
            "双扇平开窗.dxf": "casement_window",
            "推拉窗.dxf": "sliding_window",
        }
        for file_name, expected in cases.items():
            with self.subTest(file_name=file_name):
                entities = parse_dxf(legend_path(file_name))
                segments = extract_segments(entities)
                rects = suppress_nested_elevation_rectangles(elevation_line_rectangles(segments, None))
                self.assertTrue(rects)
                category = elevation_component_category("window", rects[0], segments)
                self.assertEqual(category, expected)

    def test_unclassified_elevation_window_category_is_unknown(self) -> None:
        entities = [
            CadEntity("LINE", "A-WALL", {"start": (0, 0), "end": (3000, 0)}),
            CadEntity("LINE", "A-WALL", {"start": (3000, 0), "end": (3000, 3000)}),
            CadEntity("LINE", "A-WALL", {"start": (3000, 3000), "end": (0, 3000)}),
            CadEntity("LINE", "A-WALL", {"start": (0, 3000), "end": (0, 0)}),
            CadEntity("LINE", "A-WINDOW", {"start": (1000, 900), "end": (2000, 900)}),
            CadEntity("LINE", "A-WINDOW", {"start": (2000, 900), "end": (2000, 2100)}),
            CadEntity("LINE", "A-WINDOW", {"start": (2000, 2100), "end": (1000, 2100)}),
            CadEntity("LINE", "A-WINDOW", {"start": (1000, 2100), "end": (1000, 900)}),
        ]
        segments = extract_segments(entities)
        rects = suppress_nested_elevation_rectangles(elevation_line_rectangles(segments, None))

        self.assertTrue(rects)
        self.assertEqual(elevation_component_category("window", rects[0], segments), "unknown")

    def test_sliding_door_legend_is_classified(self) -> None:
        entities = parse_dxf(legend_path("推拉门.dxf"))

        result = recognize(entities)
        doors = [opening for opening in result["openings"] if opening["kind"] == "door"]

        self.assertEqual(len(doors), 1)
        self.assertEqual(doors[0]["source"], "sliding_door_double_rectangles")
        self.assertEqual(doors[0]["component_category"], "sliding_door")

    def test_sliding_door_on_door_layer_is_not_split(self) -> None:
        entities = [
            CadEntity("LINE", "A-DOOR", {"start": (0, 50), "end": (900, 50)}),
            CadEntity("LINE", "A-DOOR", {"start": (900, 50), "end": (900, 100)}),
            CadEntity("LINE", "A-DOOR", {"start": (900, 100), "end": (0, 100)}),
            CadEntity("LINE", "A-DOOR", {"start": (0, 100), "end": (0, 50)}),
            CadEntity("LINE", "A-DOOR", {"start": (900, 50), "end": (1800, 50)}),
            CadEntity("LINE", "A-DOOR", {"start": (1800, 50), "end": (1800, 0)}),
            CadEntity("LINE", "A-DOOR", {"start": (1800, 0), "end": (900, 0)}),
            CadEntity("LINE", "A-DOOR", {"start": (900, 0), "end": (900, 50)}),
        ]

        result = recognize(entities)
        doors = [opening for opening in result["openings"] if opening["kind"] == "door"]

        self.assertEqual(len(doors), 1)
        self.assertEqual(doors[0]["source"], "sliding_door_double_rectangles")
        self.assertEqual(doors[0]["component_category"], "sliding_door")
        self.assertEqual(doors[0]["width"], 1800)

    def test_double_swing_arcs_without_wall_host_are_not_split(self) -> None:
        entities = [
            CadEntity("ARC", "A-DOOR", {"center": (0, 0), "radius": 900, "start_angle": 0, "end_angle": 90}),
            CadEntity("ARC", "A-DOOR", {"center": (1800, 0), "radius": 900, "start_angle": 90, "end_angle": 180}),
        ]

        result = recognize(entities)
        doors = [opening for opening in result["openings"] if opening["kind"] == "door"]

        self.assertEqual(len(doors), 1)
        self.assertEqual(doors[0]["source"], "double_swing_arc")
        self.assertEqual(doors[0]["component_category"], "double_swing_door")
        self.assertEqual(doors[0]["width"], 1800)

    def test_world_coordinate_block_door_is_located_and_classified(self) -> None:
        entities = [
            CadEntity("LINE", "A-WALL", {"start": (0, 0), "end": (3000, 0)}),
            CadEntity("LINE", "A-WALL", {"start": (0, 300), "end": (3000, 300)}),
            CadEntity(
                "INSERT",
                "洞口",
                {
                    "name": "双扇平开门",
                    "point": (1500, 150),
                    "block_bounds": (600, 0, 2400, 300),
                    "block_layers": ["Door"],
                },
            ),
            CadEntity(
                "INSERT",
                "洞口",
                {
                    "name": "TLM1",
                    "point": (1500, 150),
                    "block_bounds": (600, 0, 2400, 300),
                    "block_layers": ["Door"],
                },
            ),
        ]

        result = recognize(entities)
        doors = [opening for opening in result["openings"] if opening["kind"] == "door"]

        self.assertEqual(len(doors), 1)
        self.assertEqual(doors[0]["source"], "block")
        self.assertEqual(doors[0]["component_category"], "double_swing_door")
        self.assertEqual(doors[0]["width"], 1800)

    def test_common_block_name_aliases_are_classified(self) -> None:
        entities = [
            CadEntity("INSERT", "洞口", {"name": "sliding door", "point": (0, 0)}),
            CadEntity("INSERT", "洞口", {"name": "DD", "point": (2000, 0)}),
            CadEntity("INSERT", "洞口", {"name": "C1225", "point": (4000, 0)}),
            CadEntity("INSERT", "洞口", {"name": "SW", "point": (6000, 0)}),
        ]

        result = recognize(entities)
        openings = result["openings"]

        self.assertEqual([opening["kind"] for opening in openings], ["door", "door", "window", "window"])
        self.assertEqual(openings[0]["component_category"], "sliding_door")
        self.assertEqual(openings[1]["component_category"], "double_swing_door")
        self.assertEqual(openings[2]["component_category"], "unknown")
        self.assertEqual(openings[3]["component_category"], "sliding_window")

    def test_block_name_mark_supplies_opening_size(self) -> None:
        entities = [
            CadEntity("INSERT", "0", {"name": "M1824", "point": (0, 0)}),
            CadEntity("INSERT", "0", {"name": "sliding door 0921", "point": (2500, 0)}),
            CadEntity("INSERT", "0", {"name": "C1225", "point": (5000, 0)}),
        ]

        result = recognize(entities)
        openings = result["openings"]

        self.assertEqual([opening["kind"] for opening in openings], ["door", "door", "window"])
        self.assertEqual(openings[0]["width"], 1800)
        self.assertEqual(openings[0]["height_mm"], 2400)
        self.assertEqual(openings[0]["annotation"], "M1824")
        self.assertEqual(openings[0]["height_source"], "block_name_mark")
        self.assertEqual(openings[1]["width"], 900)
        self.assertEqual(openings[1]["height_mm"], 2100)
        self.assertEqual(openings[2]["width"], 1200)
        self.assertEqual(openings[2]["height_mm"], 2500)

    def test_adjacent_door_frames_without_wall_between_merge_as_sliding_door(self) -> None:
        openings = [
            Opening("O0007", "door", (8625.0, 0.0), 700, "A-DOOR", None, "W0003", 0.78, open_direction="east", component_category="single_swing_door", source="quarter_arc"),
            Opening("O0008", "door", (9325.0, 0.0), 700, "A-DOOR", None, "W0014", 0.78, open_direction="north", component_category="single_swing_door", source="quarter_arc"),
        ]

        merged = merge_adjacent_door_frames(openings, [])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].component_category, "sliding_door")
        self.assertEqual(merged[0].source, "merged_adjacent_door_frames")
        self.assertEqual(merged[0].point, (8975.0, 0.0))
        self.assertEqual(merged[0].width, 1400)

    def test_staggered_adjacent_door_frames_merge_as_sliding_door(self) -> None:
        openings = [
            Opening("O0007", "door", (8275.0, 350.0), 700, "A-DOOR", None, "W0013", 0.78, open_direction="east", component_category="single_swing_door", source="quarter_arc"),
            Opening("O0008", "door", (9325.0, 0.0), 700, "A-DOOR", None, "W0017", 0.78, open_direction="north", component_category="single_swing_door", source="quarter_arc"),
        ]

        merged = merge_adjacent_door_frames(openings, [])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].component_category, "sliding_door")
        self.assertEqual(merged[0].source, "merged_adjacent_door_frames")
        self.assertEqual(merged[0].width, 1750.0)

    def test_adjacent_door_frames_do_not_merge_when_wall_between(self) -> None:
        openings = [
            Opening("O0007", "door", (8625.0, 0.0), 700, "A-DOOR", None, "W0003", 0.78, component_category="single_swing_door", source="quarter_arc"),
            Opening("O0008", "door", (9325.0, 0.0), 700, "A-DOOR", None, "W0014", 0.78, component_category="single_swing_door", source="quarter_arc"),
        ]
        walls = [
            Wall("W0099", (8975.0, -250.0), (8975.0, 250.0), 500, 200, 200, "standard", "paired_wall_lines", ["A-WALL"], 0.95),
        ]

        merged = merge_adjacent_door_frames(openings, walls)

        self.assertEqual(len(merged), 2)
        self.assertEqual([opening.source for opening in merged], ["quarter_arc", "quarter_arc"])


if __name__ == "__main__":
    unittest.main()
