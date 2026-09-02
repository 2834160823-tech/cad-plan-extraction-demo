from __future__ import annotations

from .dxf_parser import CadEntity
from .geometry import Segment, bbox, distance
from .recognizer import (
    DOOR_LAYER_RE,
    WALL_LAYER_RE,
    WINDOW_LAYER_RE,
    extract_segments,
    opening_category_from_block,
    opening_kind_from_block,
    round_point,
)


MIN_ELEVATION_OPENING_SIZE = 300.0
MAX_ELEVATION_OPENING_SIZE = 6000.0
LINE_MATCH_TOLERANCE = 12.0
DOOR_SILL_TOLERANCE = 350.0


def replace_elevation_openings(entities: list[CadEntity], result: dict) -> None:
    if result.get("notes", {}).get("drawing_type") != "architectural_elevation":
        return
    result["openings"] = recognize_elevation_openings(entities)
    result.setdefault("counts", {})["openings"] = len(result["openings"])


def add_elevation_window_rectangles(entities: list[CadEntity], result: dict) -> None:
    replace_elevation_openings(entities, result)


def recognize_elevation_openings(entities: list[CadEntity]) -> list[dict]:
    segments = extract_segments(entities)
    wall_box = elevation_wall_box(segments)
    rects = suppress_nested_elevation_rectangles(
        elevation_line_rectangles(segments, wall_box) + elevation_insert_rectangles(entities, wall_box)
    )
    openings: list[dict] = []
    for rect in rects:
        kind = rect.get("kind") or classify_elevation_opening(rect, wall_box)
        sill = elevation_sill_height(rect, wall_box)
        facade_local_point = local_point_in_wall_box(rect["center"], wall_box)
        openings.append(
            {
                "id": f"O{len(openings) + 1:04d}",
                "kind": kind,
                "point": round_point(rect["center"]),
                "facade_local_point": round_point(facade_local_point) if facade_local_point is not None else None,
                "width": round(rect["width"], 3),
                "layer": rect["layer"],
                "block_name": None,
                "host_wall_id": None,
                "confidence": 0.86 if wall_box is not None else 0.72,
                "height_mm": round(rect["height"], 3),
                "annotation": None,
                "open_direction": None,
                "sill_height_mm": 0 if kind == "door" else round(sill, 3) if sill is not None else None,
                "component_category": rect.get("component_category") or elevation_component_category(kind, rect, segments),
                "source": rect.get("source") or "elevation_rectangle",
            }
        )
    return openings


def local_point_in_wall_box(
    point: tuple[float, float],
    wall_box: tuple[float, float, float, float] | None,
) -> tuple[float, float] | None:
    if wall_box is None:
        return None
    return point[0] - wall_box[0], point[1] - wall_box[1]


def elevation_wall_box(segments: list[Segment]) -> tuple[float, float, float, float] | None:
    points: list[tuple[float, float]] = []
    for seg in segments:
        if WALL_LAYER_RE.search(seg.layer):
            points.extend([seg.start, seg.end])
    if not points:
        return None
    return bbox(points)


def elevation_line_rectangles(
    segments: list[Segment], wall_box: tuple[float, float, float, float] | None
) -> list[dict]:
    layer_filtered = [
        seg
        for seg in segments
        if seg.orientation in {"H", "V"}
        and (DOOR_LAYER_RE.search(seg.layer) or WINDOW_LAYER_RE.search(seg.layer))
        and MIN_ELEVATION_OPENING_SIZE <= seg.length <= MAX_ELEVATION_OPENING_SIZE
    ]
    opening_segments = layer_filtered or [
        seg
        for seg in segments
        if seg.orientation in {"H", "V"} and MIN_ELEVATION_OPENING_SIZE <= seg.length <= MAX_ELEVATION_OPENING_SIZE
    ]
    horizontals = [seg for seg in opening_segments if seg.orientation == "H"]
    verticals = [seg for seg in opening_segments if seg.orientation == "V"]
    rects: list[dict] = []

    for left_index, left in enumerate(verticals):
        for right in verticals[left_index + 1 :]:
            x1 = left.start[0]
            x2 = right.start[0]
            if abs(x2 - x1) < MIN_ELEVATION_OPENING_SIZE:
                continue
            min_x, max_x = sorted([x1, x2])
            y1_min, y1_max = sorted([left.start[1], left.end[1]])
            y2_min, y2_max = sorted([right.start[1], right.end[1]])
            if abs(y1_min - y2_min) > LINE_MATCH_TOLERANCE or abs(y1_max - y2_max) > LINE_MATCH_TOLERANCE:
                continue
            min_y = (y1_min + y2_min) / 2
            max_y = (y1_max + y2_max) / 2
            width = max_x - min_x
            height = max_y - min_y
            if not valid_elevation_rectangle_size(width, height):
                continue
            bottom = matching_horizontal(horizontals, min_x, max_x, min_y)
            top = matching_horizontal(horizontals, min_x, max_x, max_y)
            if bottom is None or top is None:
                continue
            rect = {
                "min_x": min_x,
                "min_y": min_y,
                "max_x": max_x,
                "max_y": max_y,
                "width": width,
                "height": height,
                "area": width * height,
                "center": ((min_x + max_x) / 2, (min_y + max_y) / 2),
                "layer": ";".join(sorted({left.layer, right.layer, bottom.layer, top.layer})),
            }
            if not rectangle_in_wall_box(rect, wall_box):
                continue
            rects.append(rect)
    return unique_rectangles(rects)


def elevation_insert_rectangles(
    entities: list[CadEntity],
    wall_box: tuple[float, float, float, float] | None,
) -> list[dict]:
    rects: list[dict] = []
    for ent in entities:
        if ent.type != "INSERT":
            continue
        kind = opening_kind_from_block(ent)
        if kind not in {"door", "window"}:
            continue
        data = ent.data or {}
        bounds = data.get("block_bounds")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            continue
        min_x, min_y, max_x, max_y = sorted_pair_bounds(bounds)
        width = max_x - min_x
        height = max_y - min_y
        if not valid_elevation_rectangle_size(width, height):
            continue
        rect = {
            "min_x": min_x,
            "min_y": min_y,
            "max_x": max_x,
            "max_y": max_y,
            "width": width,
            "height": height,
            "area": width * height,
            "center": ((min_x + max_x) / 2, (min_y + max_y) / 2),
            "layer": ";".join([str(ent.layer), str(data.get("name") or "")] + [str(layer) for layer in data.get("block_layers", [])]),
            "kind": kind,
            "component_category": opening_category_from_block(kind, str(data.get("name") or "")),
            "source": "elevation_block_bounds",
        }
        if not rectangle_in_wall_box(rect, wall_box):
            continue
        rects.append(rect)
    return unique_rectangles(rects)


def sorted_pair_bounds(bounds: object) -> tuple[float, float, float, float]:
    values = [float(value) for value in bounds]  # type: ignore[arg-type]
    min_x, max_x = sorted([values[0], values[2]])
    min_y, max_y = sorted([values[1], values[3]])
    return min_x, min_y, max_x, max_y


def matching_horizontal(horizontals: list[Segment], min_x: float, max_x: float, y: float) -> Segment | None:
    for seg in horizontals:
        h_min_x, h_max_x = sorted([seg.start[0], seg.end[0]])
        h_y = seg.start[1]
        if abs(h_y - y) > LINE_MATCH_TOLERANCE:
            continue
        if abs(h_min_x - min_x) <= LINE_MATCH_TOLERANCE and abs(h_max_x - max_x) <= LINE_MATCH_TOLERANCE:
            return seg
    return None


def valid_elevation_rectangle_size(width: float, height: float) -> bool:
    return (
        MIN_ELEVATION_OPENING_SIZE <= width <= MAX_ELEVATION_OPENING_SIZE
        and MIN_ELEVATION_OPENING_SIZE <= height <= MAX_ELEVATION_OPENING_SIZE
    )


def rectangle_in_wall_box(rect: dict, wall_box: tuple[float, float, float, float] | None) -> bool:
    if wall_box is None:
        return True
    min_x, min_y, max_x, max_y = wall_box
    padding = 300.0
    return (
        rect["min_x"] >= min_x - padding
        and rect["max_x"] <= max_x + padding
        and rect["min_y"] >= min_y - padding
        and rect["max_y"] <= max_y + padding
    )


def classify_elevation_opening(rect: dict, wall_box: tuple[float, float, float, float] | None) -> str:
    if wall_box is not None:
        floor_y = wall_box[1]
        if abs(rect["min_y"] - floor_y) <= DOOR_SILL_TOLERANCE and rect["height"] >= 1400:
            return "door"
        return "window"
    if WINDOW_LAYER_RE.search(rect["layer"]) and not DOOR_LAYER_RE.search(rect["layer"]):
        return "window"
    return "door" if rect["height"] >= 1400 else "window"


def elevation_component_category(kind: str, rect: dict, segments: list[Segment]) -> str:
    if kind == "door":
        return "unknown"
    internal = internal_segments(rect, segments)
    if count_arrow_heads(internal) >= 2:
        return "sliding_window"
    if count_casement_v_shapes(rect, internal) >= 1:
        return "casement_window"
    return "unknown"


def internal_segments(rect: dict, segments: list[Segment]) -> list[Segment]:
    rows: list[Segment] = []
    for seg in segments:
        if segment_on_rectangle_border(seg, rect):
            continue
        points = [seg.start, seg.end, seg.midpoint]
        if all(rect["min_x"] - LINE_MATCH_TOLERANCE <= p[0] <= rect["max_x"] + LINE_MATCH_TOLERANCE for p in points) and all(
            rect["min_y"] - LINE_MATCH_TOLERANCE <= p[1] <= rect["max_y"] + LINE_MATCH_TOLERANCE for p in points
        ):
            rows.append(seg)
    return rows


def segment_on_rectangle_border(seg: Segment, rect: dict) -> bool:
    if seg.orientation == "H":
        min_x, max_x = sorted([seg.start[0], seg.end[0]])
        return (
            abs(seg.start[1] - rect["min_y"]) <= LINE_MATCH_TOLERANCE
            or abs(seg.start[1] - rect["max_y"]) <= LINE_MATCH_TOLERANCE
        ) and abs(min_x - rect["min_x"]) <= LINE_MATCH_TOLERANCE and abs(max_x - rect["max_x"]) <= LINE_MATCH_TOLERANCE
    if seg.orientation == "V":
        min_y, max_y = sorted([seg.start[1], seg.end[1]])
        return (
            abs(seg.start[0] - rect["min_x"]) <= LINE_MATCH_TOLERANCE
            or abs(seg.start[0] - rect["max_x"]) <= LINE_MATCH_TOLERANCE
        ) and abs(min_y - rect["min_y"]) <= LINE_MATCH_TOLERANCE and abs(max_y - rect["max_y"]) <= LINE_MATCH_TOLERANCE
    return False


def count_arrow_heads(segments: list[Segment]) -> int:
    count = 0
    candidates = [seg for seg in segments if 15 <= seg.length <= 260]
    for base in candidates:
        joined = [
            other
            for other in candidates
            if other is not base and (distance(other.start, base.start) <= 8 or distance(other.end, base.start) <= 8)
        ]
        if len(joined) >= 2:
            count += 1
    return count


def count_casement_v_shapes(rect: dict, segments: list[Segment]) -> int:
    diagonals = [seg for seg in segments if seg.orientation == "OTHER" and seg.length >= min(rect["width"], rect["height"]) * 0.45]
    count = 0
    used: set[int] = set()
    for i, a in enumerate(diagonals):
        if i in used:
            continue
        for j, b in enumerate(diagonals[i + 1 :], start=i + 1):
            if j in used:
                continue
            if share_endpoint(a, b) and not share_endpoint_on_corner(rect, a, b):
                count += 1
                used.update({i, j})
                break
    return count


def share_endpoint(a: Segment, b: Segment) -> bool:
    return any(distance(pa, pb) <= 8 for pa in [a.start, a.end] for pb in [b.start, b.end])


def share_endpoint_on_corner(rect: dict, a: Segment, b: Segment) -> bool:
    corners = [
        (rect["min_x"], rect["min_y"]),
        (rect["min_x"], rect["max_y"]),
        (rect["max_x"], rect["min_y"]),
        (rect["max_x"], rect["max_y"]),
    ]
    for pa in [a.start, a.end]:
        for pb in [b.start, b.end]:
            if distance(pa, pb) <= 8 and any(distance(pa, corner) <= 8 for corner in corners):
                return True
    return False


def elevation_sill_height(rect: dict, wall_box: tuple[float, float, float, float] | None) -> float | None:
    if wall_box is None:
        return None
    return max(0.0, rect["min_y"] - wall_box[1])


def suppress_nested_elevation_rectangles(rects: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for rect in sorted(rects, key=lambda item: item["area"], reverse=True):
        if any(same_elevation_opening(existing, rect) for existing in kept):
            continue
        kept.append(rect)
    kept.sort(key=lambda item: (item["min_x"], item["min_y"], item["max_x"], item["max_y"]))
    return kept


def same_elevation_opening(existing: dict, candidate: dict) -> bool:
    if distance(existing["center"], candidate["center"]) <= 160:
        return True
    return rectangle_contains(existing, candidate) or rectangle_contains(candidate, existing)


def rectangle_contains(outer: dict, inner: dict) -> bool:
    return (
        outer["min_x"] <= inner["min_x"] + LINE_MATCH_TOLERANCE
        and outer["min_y"] <= inner["min_y"] + LINE_MATCH_TOLERANCE
        and outer["max_x"] >= inner["max_x"] - LINE_MATCH_TOLERANCE
        and outer["max_y"] >= inner["max_y"] - LINE_MATCH_TOLERANCE
    )


def unique_rectangles(rects: list[dict]) -> list[dict]:
    unique: list[dict] = []
    seen: set[tuple[int, int, int, int]] = set()
    for rect in rects:
        key = tuple(int(round(rect[name] / LINE_MATCH_TOLERANCE)) for name in ["min_x", "min_y", "max_x", "max_y"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(rect)
    return unique
