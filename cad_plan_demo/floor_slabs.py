from __future__ import annotations

import re

from .dxf_parser import CadEntity
from .geometry import bbox, polygon_area


DEFAULT_FLOOR_THICKNESS = 120.0
OPENING_ONLY_PADDING = 1000.0
SLAB_LAYER_RE = re.compile(r"阳台板|楼板|balcony[\s_-]*slab|floor[\s_-]*slab|slab|deck", re.I)
SLAB_EXCLUDED_LAYER_RE = re.compile(r"洞口|opening|void", re.I)
LOOP_SNAP_TOLERANCE = 20.0


def build_default_floor_slabs(result: dict, entities: list[CadEntity] | None = None) -> None:
    openings = result.get("floor_openings", [])
    is_plan = result.get("notes", {}).get("drawing_type") == "architectural_plan"
    has_parapets = bool(result.get("parapets", []))
    explicit_floors = explicit_floor_slabs(entities or [], result)
    if not is_plan and not openings and not has_parapets and not explicit_floors:
        result["floors"] = []
        result.setdefault("counts", {})["floors"] = 0
        return

    boundary, source, confidence = default_floor_boundary(result)
    floors: list[dict] = []
    if boundary is not None:
        floors.append(
            {
                "id": "FLOOR0001",
                "floor_type": "default_floor_slab",
                "local_boundary_points": boundary,
                "area": round(polygon_area([tuple(point) for point in boundary]), 3),
                "thickness_mm": DEFAULT_FLOOR_THICKNESS,
                "elevation_mm": 0,
                "source": source,
                "confidence": confidence,
                "opening_ids": [opening.get("id") for opening in openings if opening.get("id")],
                "opening_count": len(openings),
                "needs_review": source != "wall_bbox_default_slab",
                "remarks": "Default slab boundary inferred because slab outline is not drawn in plan.",
            }
        )

    for explicit_floor in explicit_floors:
        if any(same_boundary_bbox(explicit_floor["local_boundary_points"], floor["local_boundary_points"]) for floor in floors):
            floors = [
                floor
                for floor in floors
                if not same_boundary_bbox(explicit_floor["local_boundary_points"], floor["local_boundary_points"])
            ]
        explicit_floor["id"] = f"FLOOR{len(floors) + 1:04d}"
        floors.append(explicit_floor)

    if not floors:
        result["floors"] = []
        result.setdefault("counts", {})["floors"] = 0
        return

    result["floors"] = floors
    result.setdefault("counts", {})["floors"] = len(floors)
    for opening in openings:
        host = floor_containing_opening(floors, opening) or floors[0]
        opening["host_floor_id"] = host["id"]


def explicit_floor_slabs(entities: list[CadEntity], result: dict) -> list[dict]:
    edges: list[tuple[tuple[float, float], tuple[float, float], str]] = []
    for ent in entities:
        layer = ent.layer or ""
        if not SLAB_LAYER_RE.search(layer) or SLAB_EXCLUDED_LAYER_RE.search(layer):
            continue
        data = ent.data or {}
        if ent.type == "LINE":
            start = point_tuple(data.get("start"))
            end = point_tuple(data.get("end"))
            if start and end:
                edges.append((start, end, layer))
        elif ent.type in {"LWPOLYLINE", "POLYLINE"}:
            points = [point_tuple(item) for item in data.get("points", [])]
            clean = [point for point in points if point is not None]
            edges.extend((start, end, layer) for start, end in zip(clean, clean[1:]))
            if data.get("closed") and len(clean) > 2:
                edges.append((clean[-1], clean[0], layer))

    origin = tuple(result.get("coordinate_system", {}).get("origin", (0.0, 0.0)))
    floors: list[dict] = []
    for points, layers in closed_edge_loops(edges):
        local_boundary = [
            [round(point[0] - float(origin[0]), 3), round(point[1] - float(origin[1]), 3)]
            for point in points
        ]
        area = abs(polygon_area([tuple(point) for point in local_boundary]))
        if area < 100000:
            continue
        is_balcony = any(re.search(r"阳台|balcony", layer, re.I) for layer in layers)
        floors.append(
            {
                "id": "",
                "floor_type": "balcony_slab" if is_balcony else "explicit_floor_slab",
                "local_boundary_points": local_boundary,
                "area": round(area, 3),
                "thickness_mm": None if is_balcony else DEFAULT_FLOOR_THICKNESS,
                "elevation_mm": 0,
                "source": "dedicated_balcony_slab_layer" if is_balcony else "dedicated_slab_layer",
                "source_geometry_count": len(points),
                "confidence": 0.96,
                "opening_ids": [],
                "opening_count": 0,
                "needs_review": False,
                "remarks": "Closed slab boundary recognized from a dedicated CAD layer.",
            }
        )
    return floors


def closed_edge_loops(
    edges: list[tuple[tuple[float, float], tuple[float, float], str]],
) -> list[tuple[list[tuple[float, float]], list[str]]]:
    unused = list(edges)
    loops: list[tuple[list[tuple[float, float]], list[str]]] = []
    while unused:
        start, end, layer = unused.pop(0)
        points = [start, end]
        layers = [layer]
        while len(points) <= len(edges) + 1:
            if points_close(points[-1], points[0]) and len(points) >= 4:
                loops.append((points[:-1], layers))
                break
            match_index = None
            next_point = None
            for index, (edge_start, edge_end, edge_layer) in enumerate(unused):
                if points_close(points[-1], edge_start):
                    match_index, next_point, layer = index, edge_end, edge_layer
                    break
                if points_close(points[-1], edge_end):
                    match_index, next_point, layer = index, edge_start, edge_layer
                    break
            if match_index is None or next_point is None:
                break
            unused.pop(match_index)
            points.append(next_point)
            layers.append(layer)
    return loops


def same_boundary_bbox(a: list[list[float]], b: list[list[float]], tolerance: float = 50.0) -> bool:
    if not a or not b:
        return False
    box_a = bbox([tuple(point) for point in a])
    box_b = bbox([tuple(point) for point in b])
    return all(abs(value_a - value_b) <= tolerance for value_a, value_b in zip(box_a, box_b))


def floor_containing_opening(floors: list[dict], opening: dict) -> dict | None:
    center = opening.get("local_center") or opening.get("center")
    point = point_tuple(center)
    if point is None:
        return None
    for floor in floors:
        boundary = [point_tuple(item) for item in floor.get("local_boundary_points", [])]
        clean = [item for item in boundary if item is not None]
        if point_inside_polygon(point, clean):
            return floor
    return None


def point_inside_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False
    inside = False
    j = len(polygon) - 1
    for i, current in enumerate(polygon):
        previous = polygon[j]
        crosses = (current[1] > point[1]) != (previous[1] > point[1])
        if crosses:
            x_at_y = (previous[0] - current[0]) * (point[1] - current[1]) / (previous[1] - current[1]) + current[0]
            if point[0] < x_at_y:
                inside = not inside
        j = i
    return inside


def point_tuple(value: object) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def points_close(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) <= LOOP_SNAP_TOLERANCE and abs(a[1] - b[1]) <= LOOP_SNAP_TOLERANCE


def default_floor_boundary(result: dict) -> tuple[list[list[float]], str, float] | tuple[None, str, float]:
    wall_points = []
    for wall in result.get("walls", []):
        for key in ["local_start", "local_end"]:
            point = wall.get(key)
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                wall_points.append((float(point[0]), float(point[1])))
    if wall_points:
        return rectangle_boundary(wall_points), "wall_bbox_default_slab", 0.72

    parapet_points = []
    for parapet in result.get("parapets", []):
        for key in ["local_start", "local_end"]:
            point = parapet.get(key)
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                parapet_points.append((float(point[0]), float(point[1])))
    if parapet_points:
        return rectangle_boundary(parapet_points), "parapet_bbox_default_slab", 0.72

    opening_points = []
    for opening in result.get("floor_openings", []):
        for point in opening.get("local_boundary_points", []):
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                opening_points.append((float(point[0]), float(point[1])))
    if opening_points:
        return rectangle_boundary(opening_points, OPENING_ONLY_PADDING), "floor_opening_bbox_default_slab", 0.45

    frame = result.get("frame", {})
    if all(key in frame for key in ["min_x", "min_y", "max_x", "max_y"]):
        coord = result.get("coordinate_system", {})
        origin = coord.get("origin", [0.0, 0.0])
        points = [
            (float(frame["min_x"]) - float(origin[0]), float(frame["min_y"]) - float(origin[1])),
            (float(frame["max_x"]) - float(origin[0]), float(frame["min_y"]) - float(origin[1])),
            (float(frame["max_x"]) - float(origin[0]), float(frame["max_y"]) - float(origin[1])),
            (float(frame["min_x"]) - float(origin[0]), float(frame["max_y"]) - float(origin[1])),
        ]
        return [[round(x, 3), round(y, 3)] for x, y in points], "frame_bbox_default_slab", 0.35

    return None, "", 0.0


def rectangle_boundary(points: list[tuple[float, float]], padding: float = 0.0) -> list[list[float]]:
    min_x, min_y, max_x, max_y = bbox(points)
    min_x -= padding
    min_y -= padding
    max_x += padding
    max_y += padding
    return [
        [round(min_x, 3), round(min_y, 3)],
        [round(max_x, 3), round(min_y, 3)],
        [round(max_x, 3), round(max_y, 3)],
        [round(min_x, 3), round(max_y, 3)],
    ]
