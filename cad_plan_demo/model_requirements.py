from __future__ import annotations

from math import hypot
from statistics import median
from typing import Any

from .geometry import distance
from .opening_marks import iter_mark_dimensions, normalize_mark_text


def apply_requirement_enrichment(result: dict) -> None:
    coordinate_system = build_coordinate_system(result.get("axes", []))
    result["coordinate_system"] = coordinate_system
    add_relative_coordinates(result, coordinate_system)
    attach_wall_heights(result)
    attach_opening_annotations(result)


def build_coordinate_system(axes: list[dict]) -> dict:
    vertical = [axis for axis in axes if axis_orientation(axis) == "V"]
    horizontal = [axis for axis in axes if axis_orientation(axis) == "H"]
    if not vertical or not horizontal:
        return {
            "origin": [0.0, 0.0],
            "source": "fallback_world_origin",
            "origin_axis_x": None,
            "origin_axis_y": None,
            "confidence": 0.25,
        }

    left_axis = min(vertical, key=lambda axis: min(axis["start"][0], axis["end"][0]))
    bottom_axis = min(horizontal, key=lambda axis: min(axis["start"][1], axis["end"][1]))
    origin = [axis_x(left_axis), axis_y(bottom_axis)]
    return {
        "origin": [round(origin[0], 3), round(origin[1], 3)],
        "source": "left_bottom_axis_intersection",
        "origin_axis_x": left_axis.get("name") or left_axis.get("id"),
        "origin_axis_y": bottom_axis.get("name") or bottom_axis.get("id"),
        "confidence": 0.88,
    }


def add_relative_coordinates(result: dict, coordinate_system: dict) -> None:
    origin = tuple(coordinate_system.get("origin", [0.0, 0.0]))
    for axis in result.get("axes", []):
        add_relative_point_fields(axis, origin, "start", "local_start")
        add_relative_point_fields(axis, origin, "end", "local_end")
    for wall in result.get("walls", []):
        add_relative_point_fields(wall, origin, "start", "local_start")
        add_relative_point_fields(wall, origin, "end", "local_end")
    for parapet in result.get("parapets", []):
        add_relative_point_fields(parapet, origin, "start", "local_start")
        add_relative_point_fields(parapet, origin, "end", "local_end")
    for opening in result.get("openings", []):
        add_relative_point_fields(opening, origin, "point", "local_point")
        add_relative_point_fields(opening, origin, "panel_start", "local_panel_start")
        add_relative_point_fields(opening, origin, "panel_end", "local_panel_end")
    for column in result.get("columns", []):
        add_relative_point_fields(column, origin, "center", "local_center")
    for opening in result.get("floor_openings", []):
        add_relative_point_fields(opening, origin, "center", "local_center")
        add_relative_boundary_points(opening, origin, "boundary_points", "local_boundary_points")


def attach_wall_heights(result: dict) -> None:
    height = default_floor_height(result)
    if not height:
        return
    for wall in result.get("walls", []):
        wall.setdefault("height_mm", height)
        wall.setdefault("height_source", "floor_height_candidate")


def attach_opening_annotations(result: dict) -> None:
    text_items = result.get("notes", {}).get("text_items", [])
    marks = extract_marks(text_items)
    reconcile_doors_with_explicit_marks(result, marks)
    for opening in result.get("openings", []):
        if str(opening.get("annotation_source", "")).startswith("explicit_mark_"):
            if opening.get("kind") == "door":
                opening.setdefault("sill_height_mm", 0)
                opening.setdefault("sill_height_source", "door_default_floor_level")
            continue
        mark = nearest_mark(opening, marks)
        if mark is None:
            if opening.get("kind") == "door":
                opening.setdefault("sill_height_mm", 0)
                opening.setdefault("sill_height_source", "door_default_floor_level")
            continue
        opening["annotation"] = mark["text"]
        opening["annotation_source"] = "nearest_text_annotation"
        previous_width = opening.get("width")
        if previous_width is not None and abs(float(previous_width) - float(mark["width_mm"])) > 1e-6:
            opening["width_geometry_original"] = previous_width
        opening["width"] = mark["width_mm"]
        opening["width_source"] = "nearest_text_annotation"
        opening["height_mm"] = mark["height_mm"]
        opening["height_source"] = "nearest_text_annotation"
        opening["size_source"] = "nearest_text_annotation"
        if opening.get("kind") == "door":
            opening["sill_height_mm"] = 0
            opening["sill_height_source"] = "door_default_floor_level"


def reconcile_doors_with_explicit_marks(result: dict, marks: list[dict]) -> None:
    """Use repeated explicit door marks as a count check for crowded plan views.

    A drawing often contains a door symbol made from several independent line
    groups, while a few custom door blocks have no usable geometry at all.  In
    a materially inconsistent view, each explicit D/M/TLM mark is stronger
    evidence than the duplicate geometry candidates.  This intentionally only
    activates for a large discrepancy so unlabelled doors remain supported.
    """
    doors = [opening for opening in result.get("openings", []) if opening.get("kind") == "door"]
    door_marks = [mark for mark in marks if mark.get("kind") == "door"]
    if len(doors) < 5 or len(door_marks) < 5 or abs(len(doors) - len(door_marks)) < 3:
        return

    pairs = sorted(
        (
            distance(tuple(opening.get("point", (0.0, 0.0))), tuple(mark["point"])),
            opening_index,
            mark_index,
        )
        for opening_index, opening in enumerate(doors)
        for mark_index, mark in enumerate(door_marks)
    )
    matched_openings: dict[int, dict] = {}
    used_marks: set[int] = set()
    for mark_distance, opening_index, mark_index in pairs:
        if mark_distance > 2500 or opening_index in matched_openings or mark_index in used_marks:
            continue
        matched_openings[opening_index] = door_marks[mark_index]
        used_marks.add(mark_index)

    reconciled = []
    assigned_mark_hosts: list[tuple[dict, str | None, str | None]] = []
    mark_offsets: dict[str, list[tuple[float, float]]] = {}
    for index, mark in sorted(matched_openings.items()):
        opening = apply_opening_mark(dict(doors[index]), mark)
        reconciled.append(opening)
        opening_point = tuple(opening.get("point", (0.0, 0.0)))
        mark_point = tuple(mark.get("point", (0.0, 0.0)))
        mark_offsets.setdefault(str(mark.get("text", "")), []).append(
            (opening_point[0] - mark_point[0], opening_point[1] - mark_point[1])
        )
        assigned_mark_hosts.append(
            (mark, opening.get("host_wall_id"), wall_line_key(opening.get("host_wall_id"), result.get("walls", [])))
        )

    origin = tuple(result.get("coordinate_system", {}).get("origin", (0.0, 0.0)))
    for mark_index, mark in enumerate(door_marks):
        if mark_index not in used_marks:
            excluded_hosts, excluded_line_keys = nearby_assigned_hosts(mark, assigned_mark_hosts)
            inferred = door_from_unmatched_mark(
                mark,
                result.get("walls", []),
                len(reconciled) + 1,
                origin,
                excluded_hosts,
                excluded_line_keys,
                representative_mark_offset(mark, mark_offsets),
            )
            reconciled.append(inferred)
            assigned_mark_hosts.append(
                (
                    mark,
                    inferred.get("host_wall_id"),
                    wall_line_key(inferred.get("host_wall_id"), result.get("walls", [])),
                )
            )

    non_doors = [opening for opening in result.get("openings", []) if opening.get("kind") != "door"]
    for index, opening in enumerate(reconciled, start=1):
        opening["id"] = f"O{index:04d}"
    result["openings"] = reconciled + non_doors


def apply_opening_mark(opening: dict, mark: dict) -> dict:
    previous_width = opening.get("width")
    opening["annotation"] = mark["text"]
    opening["annotation_source"] = "explicit_mark_count_reconciliation"
    if previous_width is not None and abs(float(previous_width) - float(mark["width_mm"])) > 1e-6:
        opening["width_geometry_original"] = previous_width
    opening["width"] = mark["width_mm"]
    opening["width_source"] = "explicit_mark_count_reconciliation"
    opening["height_mm"] = mark["height_mm"]
    opening["height_source"] = "explicit_mark_count_reconciliation"
    opening["size_source"] = "explicit_mark_count_reconciliation"
    opening["sill_height_mm"] = 0
    opening["sill_height_source"] = "door_default_floor_level"
    return opening


def door_from_unmatched_mark(
    mark: dict,
    walls: list[dict],
    sequence: int,
    origin: tuple[float, float] = (0.0, 0.0),
    excluded_host_ids: set[str] | None = None,
    excluded_line_keys: set[str] | None = None,
    mark_offset: tuple[float, float] | None = None,
) -> dict:
    point, host_wall_id = nearest_wall_projection(
        tuple(mark["point"]),
        walls,
        excluded_host_ids=excluded_host_ids,
        excluded_line_keys=excluded_line_keys,
    )
    host_wall = next((wall for wall in walls if wall.get("id") == host_wall_id), None)
    if host_wall is not None and mark_offset is not None:
        adjusted_mark_point = apply_along_wall_offset(tuple(mark["point"]), mark_offset, host_wall)
        point = project_point_to_line_segment(
            adjusted_mark_point,
            tuple(host_wall.get("start", point)),
            tuple(host_wall.get("end", point)),
        )
    return {
        "id": f"O{sequence:04d}",
        "kind": "door",
        "point": [round(point[0], 3), round(point[1], 3)],
        "local_point": [round(point[0] - origin[0], 3), round(point[1] - origin[1], 3)],
        "width": mark["width_mm"],
        "height_mm": mark["height_mm"],
        "layer": "text_annotation",
        "host_wall_id": host_wall_id,
        "confidence": 0.45,
        "component_category": "unknown",
        "source": "door_mark_without_geometry",
        "annotation": mark["text"],
        "annotation_source": "explicit_mark_without_geometry",
        "width_source": "explicit_mark_without_geometry",
        "height_source": "explicit_mark_without_geometry",
        "size_source": "explicit_mark_without_geometry",
        "sill_height_mm": 0,
        "sill_height_source": "door_default_floor_level",
        "needs_review": True,
        "remarks": "Explicit door mark has no matched geometry candidate.",
    }


def representative_mark_offset(
    mark: dict,
    offsets: dict[str, list[tuple[float, float]]],
) -> tuple[float, float] | None:
    candidates = offsets.get(str(mark.get("text", "")), [])
    if not candidates:
        return None
    return (median(item[0] for item in candidates), median(item[1] for item in candidates))


def apply_along_wall_offset(
    point: tuple[float, float],
    offset: tuple[float, float],
    wall: dict,
) -> tuple[float, float]:
    start = tuple(wall.get("start", ()))
    end = tuple(wall.get("end", ()))
    if len(start) != 2 or len(end) != 2:
        return point
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = hypot(dx, dy)
    if length <= 1e-9:
        return point
    ux, uy = dx / length, dy / length
    along = offset[0] * ux + offset[1] * uy
    return (point[0] + along * ux, point[1] + along * uy)


def nearby_assigned_hosts(
    mark: dict,
    assignments: list[tuple[dict, str | None, str | None]],
) -> tuple[set[str], set[str]]:
    excluded_hosts: set[str] = set()
    excluded_line_keys: set[str] = set()
    point = tuple(mark.get("point", (0.0, 0.0)))
    for assigned_mark, host_id, line_key in assignments:
        other = tuple(assigned_mark.get("point", (0.0, 0.0)))
        dx = abs(point[0] - other[0])
        dy = abs(point[1] - other[1])
        if mark.get("text") != assigned_mark.get("text"):
            continue
        if min(dx, dy) > 300 or not 350 <= max(dx, dy) <= 1800:
            continue
        if host_id:
            excluded_hosts.add(host_id)
        if line_key:
            excluded_line_keys.add(line_key)
    return excluded_hosts, excluded_line_keys


def nearest_wall_projection(
    point: tuple[float, float],
    walls: list[dict],
    excluded_host_ids: set[str] | None = None,
    excluded_line_keys: set[str] | None = None,
) -> tuple[tuple[float, float], str | None]:
    excluded_host_ids = excluded_host_ids or set()
    excluded_line_keys = excluded_line_keys or set()
    best_point = point
    best_wall_id = None
    best_distance = float("inf")
    for wall in walls:
        wall_id = wall.get("id")
        if wall_id in excluded_host_ids or wall_line_key_from_wall(wall) in excluded_line_keys:
            continue
        start = tuple(wall.get("start", ()))
        end = tuple(wall.get("end", ()))
        if len(start) != 2 or len(end) != 2:
            continue
        projected = project_point_to_line_segment(point, start, end)
        candidate_distance = distance(point, projected)
        if candidate_distance < best_distance:
            best_point = projected
            best_wall_id = wall_id
            best_distance = candidate_distance
    return best_point, best_wall_id


def wall_line_key(wall_id: str | None, walls: list[dict]) -> str | None:
    wall = next((item for item in walls if item.get("id") == wall_id), None)
    return wall_line_key_from_wall(wall) if wall else None


def wall_line_key_from_wall(wall: dict) -> str | None:
    start = tuple(wall.get("start", ()))
    end = tuple(wall.get("end", ()))
    if len(start) != 2 or len(end) != 2:
        return None
    dx = abs(end[0] - start[0])
    dy = abs(end[1] - start[1])
    if dx >= dy * 10:
        return f"H:{round((start[1] + end[1]) / 2, 1)}"
    if dy >= dx * 10:
        return f"V:{round((start[0] + end[0]) / 2, 1)}"
    return None


def project_point_to_line_segment(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> tuple[float, float]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-9:
        return start
    ratio = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared
    ratio = max(0.0, min(1.0, ratio))
    return (start[0] + ratio * dx, start[1] + ratio * dy)


def extract_marks(text_items: list[dict]) -> list[dict]:
    marks: list[dict] = []
    for item in text_items:
        for mark in iter_mark_dimensions(item.get("text", "")):
            if mark.get("kind") not in {"door", "window"}:
                continue
            mark["point"] = tuple(item.get("point", (0.0, 0.0)))
            marks.append(mark)
    return marks


def nearest_mark(opening: dict, marks: list[dict], max_distance: float = 2500) -> dict | None:
    if not marks:
        return None
    point = tuple(opening.get("point", (0.0, 0.0)))
    kind = opening.get("kind")
    best = None
    best_dist = max_distance
    for mark in marks:
        if mark["kind"] != kind:
            continue
        d = distance(point, mark["point"])
        if d < best_dist:
            best = mark
            best_dist = d
    return best


def default_floor_height(result: dict) -> float | None:
    heights = result.get("plan_summary", {}).get("floor_heights", [])
    if not heights:
        return None
    try:
        return float(heights[0].get("height_mm") or 0) or None
    except (TypeError, ValueError):
        return None


def add_relative_point_fields(row: dict, origin: tuple[float, float], source_key: str, target_key: str) -> None:
    if source_key not in row:
        return
    point = row.get(source_key)
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return
    row[target_key] = [round(float(point[0]) - origin[0], 3), round(float(point[1]) - origin[1], 3)]


def add_relative_boundary_points(row: dict, origin: tuple[float, float], source_key: str, target_key: str) -> None:
    points = row.get(source_key)
    if not isinstance(points, list):
        return
    local_points = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        local_points.append([round(float(point[0]) - origin[0], 3), round(float(point[1]) - origin[1], 3)])
    if local_points:
        row[target_key] = local_points


def axis_orientation(axis: dict) -> str:
    start = axis.get("start", (0.0, 0.0))
    end = axis.get("end", (0.0, 0.0))
    dx = abs(float(end[0]) - float(start[0]))
    dy = abs(float(end[1]) - float(start[1]))
    if dy >= dx * 5:
        return "V"
    if dx >= dy * 5:
        return "H"
    return "OTHER"


def axis_x(axis: dict) -> float:
    return (float(axis["start"][0]) + float(axis["end"][0])) / 2


def axis_y(axis: dict) -> float:
    return (float(axis["start"][1]) + float(axis["end"][1])) / 2
