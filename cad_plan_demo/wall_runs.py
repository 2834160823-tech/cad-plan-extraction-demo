from __future__ import annotations

from math import atan2, cos, degrees, hypot, radians, sin
from typing import Any


ANGLE_TOLERANCE_DEGREES = 4.0
LINE_OFFSET_TOLERANCE = 260.0
TOUCHING_GAP_TOLERANCE = 220.0
MAX_OPENING_GAP = 6500.0
OPENING_LINE_TOLERANCE = 900.0


def build_logical_wall_runs(result: dict) -> None:
    walls = [wall for wall in result.get("walls", []) if point(wall.get("start")) and point(wall.get("end"))]
    openings = [opening for opening in result.get("openings", []) if opening_points(opening)]
    runs = make_wall_runs(walls, openings)
    result["wall_runs"] = runs
    result.setdefault("counts", {})["wall_runs"] = len(runs)
    result.setdefault("plan_summary", {}).setdefault("counts", {})["wall_runs"] = len(runs)
    attach_openings_to_wall_runs(openings, runs)


def make_wall_runs(walls: list[dict], openings: list[dict]) -> list[dict]:
    groups: list[dict] = []
    for wall in walls:
        axis = wall_axis(wall)
        if axis is None:
            continue
        group = matching_group(groups, axis)
        item = {"wall": wall, "axis": axis}
        if group is None:
            groups.append(
                {
                    "direction": axis["direction"],
                    "offset": axis["offset"],
                    "width": axis["width"],
                    "items": [item],
                }
            )
        else:
            group["items"].append(item)
            group["direction"] = average_direction(group["direction"], axis["direction"])
            group["offset"] = (group["offset"] * (len(group["items"]) - 1) + axis["offset"]) / len(group["items"])
            if group.get("width") is None:
                group["width"] = axis["width"]

    runs: list[dict] = []
    for group in groups:
        runs.extend(runs_for_group(group, openings))

    runs.sort(key=lambda run: (run["local_start"][0], run["local_start"][1], run["local_end"][0], run["local_end"][1]))
    for index, run in enumerate(runs, start=1):
        run["id"] = f"WR{index:04d}"
    return runs


def matching_group(groups: list[dict], axis: dict) -> dict | None:
    for group in groups:
        if angle_difference(group["direction"], axis["direction"]) > ANGLE_TOLERANCE_DEGREES:
            continue
        if abs(group["offset"] - axis["offset"]) > LINE_OFFSET_TOLERANCE:
            continue
        if group.get("width") is not None and axis["width"] is not None:
            if abs(float(group["width"]) - float(axis["width"])) > 25.0:
                continue
        return group
    return None


def runs_for_group(group: dict, openings: list[dict]) -> list[dict]:
    direction = group["direction"]
    normal = left_normal(direction)
    items = []
    for item in group["items"]:
        wall = item["wall"]
        start = point(wall.get("start"))
        end = point(wall.get("end"))
        local_start = point(wall.get("local_start")) or start
        local_end = point(wall.get("local_end")) or end
        if start is None or end is None or local_start is None or local_end is None:
            continue
        t0 = dot(local_start, direction)
        t1 = dot(local_end, direction)
        items.append(
            {
                "wall": wall,
                "min_t": min(t0, t1),
                "max_t": max(t0, t1),
                "local_start": local_start,
                "local_end": local_end,
            }
        )
    items.sort(key=lambda item: item["min_t"])

    runs: list[dict] = []
    current: list[dict] = []
    current_max_t: float | None = None
    for item in items:
        if not current:
            current = [item]
            current_max_t = item["max_t"]
            continue
        gap = item["min_t"] - float(current_max_t)
        if should_merge_gap(gap, current, item, openings, direction, normal):
            current.append(item)
            current_max_t = max(float(current_max_t), item["max_t"])
        else:
            runs.append(run_from_items(current, direction, normal, openings))
            current = [item]
            current_max_t = item["max_t"]
    if current:
        runs.append(run_from_items(current, direction, normal, openings))
    return runs


def should_merge_gap(
    gap: float,
    current: list[dict],
    next_item: dict,
    openings: list[dict],
    direction: tuple[float, float],
    normal: tuple[float, float],
) -> bool:
    if gap <= TOUCHING_GAP_TOLERANCE:
        return True
    if gap > MAX_OPENING_GAP:
        return False
    gap_start = max(item["max_t"] for item in current)
    gap_end = next_item["min_t"]
    if opening_in_gap(openings, current + [next_item], gap_start, gap_end, direction, normal):
        return True
    return gap <= 900.0


def opening_in_gap(
    openings: list[dict],
    items: list[dict],
    gap_start: float,
    gap_end: float,
    direction: tuple[float, float],
    normal: tuple[float, float],
) -> bool:
    wall_ids = {str(item["wall"].get("id")) for item in items}
    center_offset = average_item_offset(items, normal)
    for opening in openings:
        pt = opening_axis_point(opening, normal, center_offset)
        if pt is None:
            continue
        t = dot(pt, direction)
        if not (gap_start - 350.0 <= t <= gap_end + 350.0):
            continue
        if abs(dot(pt, normal) - center_offset) > OPENING_LINE_TOLERANCE:
            continue
        host = str(opening.get("host_wall_id") or "")
        if host and host in wall_ids:
            return True
        width = number(opening.get("width")) or 0.0
        if width and gap_end - gap_start <= width * 1.6 + 600.0:
            return True
        if not host:
            return True
    return False


def run_from_items(
    items: list[dict],
    direction: tuple[float, float],
    normal: tuple[float, float],
    openings: list[dict],
) -> dict:
    min_t = min(item["min_t"] for item in items)
    max_t = max(item["max_t"] for item in items)
    local_offset = average_item_offset(items, normal)
    local_start = add(scale(direction, min_t), scale(normal, local_offset))
    local_end = add(scale(direction, max_t), scale(normal, local_offset))

    first = items[0]["wall"]
    source_ids = [str(item["wall"].get("id")) for item in items]
    opening_ids = openings_for_run(openings, local_start, local_end, direction, normal, local_offset)
    world_delta = subtract(point(first.get("start")) or (0.0, 0.0), point(first.get("local_start")) or point(first.get("start")) or (0.0, 0.0))
    start = add(local_start, world_delta)
    end = add(local_end, world_delta)
    confidence_values = [number(item["wall"].get("confidence")) or 0.0 for item in items]
    return {
        "id": "",
        "start": rounded_point(start),
        "end": rounded_point(end),
        "local_start": rounded_point(local_start),
        "local_end": rounded_point(local_end),
        "length": round(max_t - min_t, 3),
        "normalized_width": round(average_number(items, "normalized_width"), 3),
        "height_mm": first_non_empty([item["wall"].get("height_mm") for item in items]),
        "source_wall_ids": source_ids,
        "source_wall_count": len(source_ids),
        "opening_ids": opening_ids,
        "opening_count": len(opening_ids),
        "direction_angle": round(degrees(atan2(direction[1], direction[0])), 3),
        "recognition_source": "logical_wall_run",
        "merge_reason": "collinear_segments_with_opening_gaps" if len(source_ids) > 1 else "single_wall_segment",
        "confidence": round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else 0.0,
    }


def openings_for_run(
    openings: list[dict],
    local_start: tuple[float, float],
    local_end: tuple[float, float],
    direction: tuple[float, float],
    normal: tuple[float, float],
    offset: float,
) -> list[str]:
    run_min = min(dot(local_start, direction), dot(local_end, direction))
    run_max = max(dot(local_start, direction), dot(local_end, direction))
    ids: list[str] = []
    for opening in openings:
        pt = opening_axis_point(opening, normal, offset)
        if pt is None:
            continue
        t = dot(pt, direction)
        if run_min - 500.0 <= t <= run_max + 500.0 and abs(dot(pt, normal) - offset) <= OPENING_LINE_TOLERANCE:
            opening_id = str(opening.get("id") or "")
            if opening_id:
                ids.append(opening_id)
    return ids


def attach_openings_to_wall_runs(openings: list[dict], runs: list[dict]) -> None:
    for opening in openings:
        best_run = None
        best_score = None
        for run in runs:
            start = point(run.get("local_start"))
            end = point(run.get("local_end"))
            if start is None or end is None:
                continue
            direction = unit_direction(start, end)
            if direction is None:
                continue
            normal = left_normal(direction)
            offset = dot(start, normal)
            pt = opening_axis_point(opening, normal, offset)
            if pt is None:
                continue
            d = point_to_segment_distance(pt, start, end)
            projection_gap = projection_outside_gap(pt, start, end)
            score = d + projection_gap * 0.25
            if best_score is None or score < best_score:
                best_score = score
                best_run = run
        if best_run is not None and best_score is not None and best_score <= OPENING_LINE_TOLERANCE:
            opening["host_wall_run_id"] = best_run["id"]


def opening_points(opening: dict) -> list[tuple[float, float]]:
    values = []
    for key in (
        "local_panel_start",
        "local_panel_end",
        "local_point",
        "panel_start",
        "panel_end",
        "point",
    ):
        candidate = point(opening.get(key))
        if candidate is not None and candidate not in values:
            values.append(candidate)
    return values


def opening_axis_point(
    opening: dict,
    normal: tuple[float, float],
    offset: float,
) -> tuple[float, float] | None:
    candidates = opening_points(opening)
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: abs(dot(candidate, normal) - offset))


def wall_axis(wall: dict) -> dict | None:
    start = point(wall.get("local_start") or wall.get("start"))
    end = point(wall.get("local_end") or wall.get("end"))
    if start is None or end is None:
        return None
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = hypot(dx, dy)
    if length <= 0:
        return None
    direction = (dx / length, dy / length)
    if direction[0] < 0 or (abs(direction[0]) < 1e-9 and direction[1] < 0):
        direction = (-direction[0], -direction[1])
    normal = left_normal(direction)
    return {
        "direction": direction,
        "offset": dot(start, normal),
        "width": number(wall.get("normalized_width")) or number(wall.get("raw_width")),
    }


def average_direction(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    x = a[0] + b[0]
    y = a[1] + b[1]
    length = hypot(x, y)
    if length <= 0:
        return a
    return (x / length, y / length)


def angle_difference(a: tuple[float, float], b: tuple[float, float]) -> float:
    angle_a = degrees(atan2(a[1], a[0])) % 180.0
    angle_b = degrees(atan2(b[1], b[0])) % 180.0
    delta = abs(angle_a - angle_b)
    return min(delta, 180.0 - delta)


def average_item_offset(items: list[dict], normal: tuple[float, float]) -> float:
    values = []
    for item in items:
        values.append(dot(item["local_start"], normal))
        values.append(dot(item["local_end"], normal))
    return sum(values) / len(values) if values else 0.0


def average_number(items: list[dict], key: str) -> float:
    values = [number(item["wall"].get(key)) for item in items]
    numeric = [value for value in values if value is not None]
    return sum(numeric) / len(numeric) if numeric else 0.0


def first_non_empty(values: list[Any]) -> Any:
    for value in values:
        if value not in {None, ""}:
            return value
    return ""


def point(value: object) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def number(value: object) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def dot(a: tuple[float, float], b: tuple[float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1]


def left_normal(direction: tuple[float, float]) -> tuple[float, float]:
    return (-direction[1], direction[0])


def unit_direction(
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[float, float] | None:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = hypot(dx, dy)
    if length <= 0:
        return None
    return (dx / length, dy / length)


def add(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] + b[0], a[1] + b[1])


def subtract(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] - b[0], a[1] - b[1])


def scale(a: tuple[float, float], factor: float) -> tuple[float, float]:
    return (a[0] * factor, a[1] * factor)


def rounded_point(value: tuple[float, float]) -> list[float]:
    return [round(value[0], 3), round(value[1], 3)]


def point_to_segment_distance(point_value: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 0:
        return hypot(point_value[0] - start[0], point_value[1] - start[1])
    t = ((point_value[0] - start[0]) * dx + (point_value[1] - start[1]) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    projection = (start[0] + dx * t, start[1] + dy * t)
    return hypot(point_value[0] - projection[0], point_value[1] - projection[1])


def projection_outside_gap(point_value: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 0:
        return 0.0
    t = ((point_value[0] - start[0]) * dx + (point_value[1] - start[1]) * dy) / length_sq
    if t < 0:
        return abs(t) * hypot(dx, dy)
    if t > 1:
        return (t - 1) * hypot(dx, dy)
    return 0.0
