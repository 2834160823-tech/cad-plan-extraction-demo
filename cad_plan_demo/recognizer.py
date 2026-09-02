from __future__ import annotations

from dataclasses import asdict, dataclass
from math import acos, atan2, cos, degrees, radians, sin
import re

from .dxf_parser import CadEntity
from .geometry import (
    Segment,
    bbox,
    centerline_between_parallel_segments,
    centroid,
    distance,
    dot,
    nearest_standard,
    normalize_segment,
    point_to_axis_distance,
    unit_direction,
)
from .opening_marks import extract_mark_dimensions


WALL_LAYER_RE = re.compile("(wall|\u5899|a[-_]?wall|buildwall)", re.I)
DOOR_LAYER_RE = re.compile("(door|\u95e8|a[-_]?door)", re.I)
WINDOW_LAYER_RE = re.compile("(window|\u7a97|win|a[-_]?window)", re.I)
OPENING_BLOCK_LAYER_RE = re.compile("(opening|\u6d1e\u53e3|\u7559\u6d1e|\u95e8\u7a97)", re.I)
AXIS_LAYER_RE = re.compile("(axis|grid|\u8f74|\u8f74\u7ebf|a[-_]?grid|center)", re.I)
COLUMN_LAYER_RE = re.compile("(column|col|pillar|\u67f1|a[-_]?col|a[-_]?column)", re.I)
FLOOR_OPENING_LAYER_RE = re.compile("(hole|void|slab[-_]?opening|floor[-_]?opening|a[-_]?hole|opening|\u6d1e\u53e3|\u7559\u6d1e|\u697c\u677f\u6d1e)", re.I)

PARAPET_LAYER_RE = re.compile(r"(parapet|\u5973\u513f\u5899|\u5973\u513f|a[-_]?parapet|p[-_]?parapet)", re.I)

DEFAULT_WALL_WIDTHS = [100, 120, 150, 180, 200, 240, 250, 300, 350, 400]
MIN_OPENING_WIDTH = 300
MAX_OPENING_WIDTH = 6000
SMALL_WALL_BLOCK_MAX_SIZE = 500.0
SMALL_WALL_CORNER_TOLERANCE = 5.0
MIN_COLUMN_SIZE = 100.0
MAX_COLUMN_SIZE = 1500.0
MIN_FLOOR_OPENING_SIZE = 150.0
MAX_FLOOR_OPENING_SIZE = 12000.0


@dataclass
class Wall:
    id: str
    start: tuple[float, float]
    end: tuple[float, float]
    length: float
    raw_width: float
    normalized_width: float
    width_status: str
    recognition_source: str
    source_layers: list[str]
    confidence: float


@dataclass
class Opening:
    id: str
    kind: str
    point: tuple[float, float]
    width: float | None
    layer: str
    block_name: str | None
    host_wall_id: str | None
    confidence: float
    height_mm: float | None = None
    annotation: str | None = None
    open_direction: str | None = None
    swing_side: str | None = None
    component_category: str | None = None
    sill_height_mm: float | None = None
    source: str | None = None
    height_source: str | None = None
    width_source: str | None = None
    annotation_source: str | None = None
    size_source: str | None = None
    panel_start: tuple[float, float] | None = None
    panel_end: tuple[float, float] | None = None
    panel_thickness_mm: float | None = None
    panel_wall_angle_deg: float | None = None
    swing_source: str | None = None
    swing_confidence: float | None = None


@dataclass
class Column:
    id: str
    column_type: str
    center: tuple[float, float]
    width: float | None
    depth: float | None
    diameter: float | None
    rotation_angle: float
    layer: str
    confidence: float
    source: str
    source_geometry_count: int


@dataclass
class FloorOpening:
    id: str
    opening_type: str
    center: tuple[float, float]
    width: float
    depth: float
    area: float
    boundary_points: list[tuple[float, float]]
    layer: str
    confidence: float
    source: str
    source_geometry_count: int


@dataclass
class Axis:
    id: str
    start: tuple[float, float]
    end: tuple[float, float]
    name: str | None
    layer: str
    confidence: float


@dataclass
class Issue:
    object_id: str
    severity: str
    message: str
    raw_value: float | None = None
    fixed_value: float | None = None


def extract_segments(entities: list[CadEntity]) -> list[Segment]:
    segments: list[Segment] = []
    counter = 1
    for ent in entities:
        if ent.type == "LINE":
            segments.append(
                normalize_segment(
                    Segment(f"S{counter:05d}", ent.layer, tuple(ent.data["start"]), tuple(ent.data["end"]), ent.type)
                )
            )
            counter += 1
        elif ent.type in {"LWPOLYLINE", "POLYLINE"}:
            pts = [tuple(p) for p in ent.data.get("points", [])]
            for a, b in zip(pts, pts[1:]):
                segments.append(normalize_segment(Segment(f"S{counter:05d}", ent.layer, a, b, ent.type)))
                counter += 1
            if ent.data.get("closed") and len(pts) > 2:
                segments.append(normalize_segment(Segment(f"S{counter:05d}", ent.layer, pts[-1], pts[0], ent.type)))
                counter += 1
    return segments


def recognize(entities: list[CadEntity]) -> dict:
    segments = extract_segments(entities)
    texts = [e for e in entities if e.type in {"TEXT", "MTEXT"}]
    inserts = [e for e in entities if e.type == "INSERT"]

    walls, issues = recognize_walls(segments)
    parapets = recognize_parapets(segments)
    axes = recognize_axes(segments, texts)
    columns = recognize_columns(entities, segments)
    floor_openings = recognize_floor_openings(entities, segments)
    openings = recognize_openings(segments, inserts, walls, entities)
    return {
        "walls": [asdict(w) for w in walls],
        "parapets": parapets,
        "openings": [asdict(o) for o in openings],
        "columns": [asdict(c) for c in columns],
        "floor_openings": [asdict(item) for item in floor_openings],
        "axes": [asdict(a) for a in axes],
        "issues": [asdict(i) for i in issues],
        "counts": {
            "entities": len(entities),
            "segments": len(segments),
            "walls": len(walls),
            "parapets": len(parapets),
            "openings": len(openings),
            "columns": len(columns),
            "floor_openings": len(floor_openings),
            "axes": len(axes),
            "issues": len(issues),
        },
    }


def recognize_columns(entities: list[CadEntity], segments: list[Segment]) -> list[Column]:
    columns: list[Column] = []

    for ent in entities:
        if not COLUMN_LAYER_RE.search(ent.layer):
            continue
        if ent.type == "CIRCLE":
            radius = float(ent.data.get("radius", 0) or 0)
            diameter = radius * 2
            if MIN_COLUMN_SIZE <= diameter <= MAX_COLUMN_SIZE:
                add_column(
                    columns,
                    Column(
                        id=f"COL{len(columns) + 1:04d}",
                        column_type="circular_column",
                        center=round_point(tuple(ent.data.get("center", (0.0, 0.0)))),
                        width=None,
                        depth=None,
                        diameter=round(diameter, 3),
                        rotation_angle=0,
                        layer=ent.layer,
                        confidence=0.9,
                        source="circle",
                        source_geometry_count=1,
                    ),
                )
        elif ent.type in {"LWPOLYLINE", "POLYLINE"} and ent.data.get("closed"):
            column = column_from_polyline(ent, len(columns) + 1)
            if column is not None:
                add_column(columns, column)

    column_segments = [seg for seg in segments if COLUMN_LAYER_RE.search(seg.layer)]
    for rect in line_rectangles(column_segments):
        column = column_from_rect(rect, len(columns) + 1, "line_rectangle", 4, 0.86)
        if column is not None:
            add_column(columns, column)
    for rect in fragmented_line_rectangles(column_segments):
        column = column_from_rect(rect, len(columns) + 1, "fragmented_line_rectangle", len(rect.get("segment_ids", [])), 0.84)
        if column is not None:
            add_column(columns, column)

    return columns


def column_from_polyline(ent: CadEntity, index: int) -> Column | None:
    points = [tuple(point) for point in ent.data.get("points", [])]
    if len(points) < 4:
        return None
    rect = axis_aligned_rect_from_points(points)
    if rect is None:
        return None
    return column_from_rect(rect, index, "closed_polyline_rectangle", len(points), 0.9, ent.layer)


def axis_aligned_rect_from_points(points: list[tuple[float, float]], tolerance: float = 8.0) -> dict | None:
    min_x, min_y, max_x, max_y = bbox(points)
    width = max_x - min_x
    height = max_y - min_y
    if not valid_column_size(width, height):
        return None
    expected = {(round(min_x, 3), round(min_y, 3)), (round(max_x, 3), round(min_y, 3)), (round(max_x, 3), round(max_y, 3)), (round(min_x, 3), round(max_y, 3))}
    actual = {(round(x, 3), round(y, 3)) for x, y in points}
    if len(actual) != 4 or any(min(distance(pt, corner) for corner in expected) > tolerance for pt in actual):
        return None
    return {
        "min_x": min_x,
        "min_y": min_y,
        "max_x": max_x,
        "max_y": max_y,
        "width": width,
        "height": height,
        "center": ((min_x + max_x) / 2, (min_y + max_y) / 2),
        "layers": [],
    }


def column_from_rect(
    rect: dict,
    index: int,
    source: str,
    source_geometry_count: int,
    confidence: float,
    layer: str | None = None,
) -> Column | None:
    width = float(rect["width"])
    depth = float(rect["height"])
    if not valid_column_size(width, depth):
        return None
    layers = rect.get("layers") or []
    return Column(
        id=f"COL{index:04d}",
        column_type="rectangular_column",
        center=round_point(tuple(rect["center"])),
        width=round(width, 3),
        depth=round(depth, 3),
        diameter=None,
        rotation_angle=0,
        layer=layer or (layers[0] if layers else ""),
        confidence=confidence,
        source=source,
        source_geometry_count=source_geometry_count,
    )


def valid_column_size(width: float, depth: float) -> bool:
    short = min(width, depth)
    long = max(width, depth)
    return MIN_COLUMN_SIZE <= short <= MAX_COLUMN_SIZE and MIN_COLUMN_SIZE <= long <= MAX_COLUMN_SIZE and long / short <= 4


def add_column(columns: list[Column], column: Column) -> None:
    if any(same_column(existing, column) for existing in columns):
        return
    column.id = f"COL{len(columns) + 1:04d}"
    columns.append(column)


def same_column(a: Column, b: Column) -> bool:
    if distance(a.center, b.center) > 20:
        return False
    if a.column_type != b.column_type:
        return False
    a_size = a.diameter or max(a.width or 0, a.depth or 0)
    b_size = b.diameter or max(b.width or 0, b.depth or 0)
    return abs(a_size - b_size) <= 20


def recognize_floor_openings(entities: list[CadEntity], segments: list[Segment]) -> list[FloorOpening]:
    hole_segments = [seg for seg in segments if FLOOR_OPENING_LAYER_RE.search(seg.layer)]
    rects = [
        rect
        for rect in line_rectangles(hole_segments)
        if valid_floor_opening_size(rect["width"], rect["height"])
    ]
    openings: list[FloorOpening] = []
    for rect in sorted(rects, key=lambda item: (item["min_x"], item["min_y"])):
        inner_count = floor_opening_inner_foldline_count(rect, hole_segments, entities)
        if inner_count <= 0:
            continue
        if any(distance(tuple(rect["center"]), item.center) < 30 for item in openings):
            continue
        boundary = [
            (rect["min_x"], rect["min_y"]),
            (rect["max_x"], rect["min_y"]),
            (rect["max_x"], rect["max_y"]),
            (rect["min_x"], rect["max_y"]),
        ]
        openings.append(
            FloorOpening(
                id=f"FOP{len(openings) + 1:04d}",
                opening_type="rectangular_floor_opening",
                center=round_point(tuple(rect["center"])),
                width=round(float(rect["width"]), 3),
                depth=round(float(rect["height"]), 3),
                area=round(float(rect["width"]) * float(rect["height"]), 3),
                boundary_points=[round_point(point) for point in boundary],
                layer=";".join(sorted(set(rect.get("layers", [])))),
                confidence=0.88,
                source="hole_layer_rectangle_with_foldline",
                source_geometry_count=4 + inner_count,
            )
        )
    return openings


def valid_floor_opening_size(width: float, depth: float) -> bool:
    return MIN_FLOOR_OPENING_SIZE <= min(width, depth) and max(width, depth) <= MAX_FLOOR_OPENING_SIZE


def floor_opening_inner_foldline_count(rect: dict, segments: list[Segment], entities: list[CadEntity]) -> int:
    polyline_count = 0
    for ent in entities:
        if ent.type not in {"LWPOLYLINE", "POLYLINE"} or ent.data.get("closed"):
            continue
        if not FLOOR_OPENING_LAYER_RE.search(ent.layer):
            continue
        pts = [tuple(point) for point in ent.data.get("points", [])]
        if len(pts) >= 3 and all(point_inside_rect(point, rect, margin=8.0) for point in pts) and has_bend_points(pts):
            polyline_count += len(pts) - 1
    if polyline_count:
        return polyline_count

    rect_segment_ids = set(rect.get("segment_ids", []))
    inner = [
        seg
        for seg in segments
        if seg.id not in rect_segment_ids
        and seg.length >= 20
        and point_inside_rect(seg.start, rect, margin=-8.0)
        and point_inside_rect(seg.end, rect, margin=-8.0)
    ]
    for i, a in enumerate(inner):
        for b in inner[i + 1 :]:
            if connected_non_collinear_segments(a, b):
                return 2
    return 0


def point_inside_rect(point: tuple[float, float], rect: dict, margin: float = 0.0) -> bool:
    x, y = point
    return rect["min_x"] + margin <= x <= rect["max_x"] - margin and rect["min_y"] + margin <= y <= rect["max_y"] - margin


def has_bend_points(points: list[tuple[float, float]]) -> bool:
    for a, b, c in zip(points, points[1:], points[2:]):
        ab = Segment("a", "", a, b, "")
        bc = Segment("b", "", b, c, "")
        if ab.length <= 0 or bc.length <= 0:
            continue
        da = unit_direction(ab)
        db = unit_direction(bc)
        if abs(dot(da, db)) < 0.98:
            return True
    return False


def connected_non_collinear_segments(a: Segment, b: Segment, tolerance: float = 8.0) -> bool:
    shared = any(distance(pa, pb) <= tolerance for pa in [a.start, a.end] for pb in [b.start, b.end])
    if not shared:
        return False
    da = unit_direction(a)
    db = unit_direction(b)
    return abs(dot(da, db)) < 0.98


def recognize_walls(
    segments: list[Segment],
    min_width: float = 60,
    max_width: float = 500,
    min_overlap: float = 500,
    short_wall_min_overlap: float = 120,
    pier_wall_min_overlap: float = 80,
    width_tolerance: float = 6,
    layer_pattern: re.Pattern[str] = WALL_LAYER_RE,
    exclude_parapet_layers: bool = True,
) -> tuple[list[Wall], list[Issue]]:
    wall_segments = unique_segments(
        [
            s
            for s in segments
            if layer_pattern.search(s.layer)
            and (not exclude_parapet_layers or not PARAPET_LAYER_RE.search(s.layer))
            and s.length >= pier_wall_min_overlap
        ]
    )
    walls: list[Wall] = []
    issues: list[Issue] = []
    used_pairs: set[tuple[str, str]] = set()
    pier_candidates: list[tuple[tuple[float, float], tuple[float, float], float, list[str], float, tuple[str, str]]] = []

    for i, a in enumerate(wall_segments):
        for b in wall_segments[i + 1 :]:
            pair_key = tuple(sorted([a.id, b.id]))
            if pair_key in used_pairs:
                continue
            candidate = centerline_between_parallel_segments(a, b)
            if candidate is None:
                continue
            start, end, width = candidate
            axis_length = Segment("tmp", "", start, end, "").length
            if min_width <= width <= max_width and axis_length >= short_wall_min_overlap:
                source = "paired_wall_lines" if axis_length >= min_overlap else "short_paired_wall_lines"
                wall = _make_wall(walls, start, end, width, [a.layer, b.layer], width_tolerance, axis_length, source)
                walls.append(wall)
                _add_width_issue(issues, wall, width_tolerance)
                used_pairs.add(pair_key)
            elif min_width <= width <= max_width and axis_length >= pier_wall_min_overlap:
                pier_candidates.append((start, end, width, [a.layer, b.layer], axis_length, pair_key))

    for start, end, width, layers, axis_length, pair_key in pier_candidates:
        if pair_key in used_pairs:
            continue
        if not _is_t_wall_pier(start, end, width, walls):
            continue
        wall = _make_wall(walls, start, end, width, layers, width_tolerance, axis_length, "t_pier_wall_lines")
        walls.append(wall)
        _add_width_issue(issues, wall, width_tolerance)
        used_pairs.add(pair_key)

    return deduplicate_walls(walls, issues)


def recognize_parapets(segments: list[Segment]) -> list[dict]:
    """Treat dedicated parapet layers as parapet geometry, never as ordinary walls."""
    detected, _ = recognize_walls(
        segments,
        layer_pattern=PARAPET_LAYER_RE,
        exclude_parapet_layers=False,
    )
    parapets: list[dict] = []
    for index, item in enumerate(detected, start=1):
        parapets.append(
            {
                "id": f"PARAPET{index:04d}",
                "start": item.start,
                "end": item.end,
                "length": item.length,
                "thickness_mm": item.normalized_width,
                "source_layers": item.source_layers,
                "source": f"dedicated_parapet_layer:{item.recognition_source}",
                "confidence": item.confidence,
                "source_geometry_count": 2,
                "needs_review": True,
            }
        )
    return parapets


def unique_segments(segments: list[Segment]) -> list[Segment]:
    unique: list[Segment] = []
    seen: set[tuple] = set()
    for seg in segments:
        key = segment_key(seg)
        if key in seen:
            continue
        seen.add(key)
        unique.append(seg)
    return unique


def segment_key(seg: Segment) -> tuple:
    start = rounded_key_point(seg.start)
    end = rounded_key_point(seg.end)
    a, b = sorted([start, end])
    return (seg.layer.lower(), a, b)


def deduplicate_walls(walls: list[Wall], issues: list[Issue]) -> tuple[list[Wall], list[Issue]]:
    unique: list[Wall] = []
    id_map: dict[str, str] = {}
    seen: dict[tuple, Wall] = {}
    for wall in walls:
        key = wall_key(wall)
        existing = seen.get(key)
        if existing is not None:
            id_map[wall.id] = existing.id
            continue
        seen[key] = wall
        unique.append(wall)

    unique = _deduplicate_small_wall_blocks(unique, id_map)

    for index, wall in enumerate(unique, start=1):
        old_id = wall.id
        wall.id = f"W{index:04d}"
        id_map[old_id] = wall.id

    deduped_issues: list[Issue] = []
    seen_issues: set[tuple] = set()
    for issue in issues:
        issue.object_id = id_map.get(issue.object_id, issue.object_id)
        key = (issue.object_id, issue.severity, issue.message, issue.raw_value, issue.fixed_value)
        if key in seen_issues:
            continue
        seen_issues.add(key)
        deduped_issues.append(issue)
    return unique, deduped_issues


def _deduplicate_small_wall_blocks(walls: list[Wall], id_map: dict[str, str]) -> list[Wall]:
    unique: list[Wall] = []
    seen_blocks: dict[tuple, Wall] = {}

    for wall in walls:
        block_key = small_wall_block_key(wall)
        if block_key is None:
            unique.append(wall)
            continue

        existing = seen_blocks.get(block_key)
        if existing is None:
            seen_blocks[block_key] = wall
            unique.append(wall)
            continue

        keep, drop = _choose_small_wall_block(existing, wall, walls)
        id_map[drop.id] = keep.id
        if keep is wall:
            seen_blocks[block_key] = wall
            for index, item in enumerate(unique):
                if item is existing:
                    unique[index] = wall
                    break

    return unique


def small_wall_block_key(wall: Wall) -> tuple | None:
    if wall.length <= 0 or wall.normalized_width <= 0:
        return None
    if max(float(wall.length), float(wall.normalized_width)) > SMALL_WALL_BLOCK_MAX_SIZE:
        return None

    dx = wall.end[0] - wall.start[0]
    dy = wall.end[1] - wall.start[1]
    axis_length = distance(wall.start, wall.end)
    if axis_length <= 0:
        return None

    nx = -dy / axis_length
    ny = dx / axis_length
    half_width = float(wall.normalized_width) / 2.0
    corners = [
        (wall.start[0] + nx * half_width, wall.start[1] + ny * half_width),
        (wall.end[0] + nx * half_width, wall.end[1] + ny * half_width),
        (wall.end[0] - nx * half_width, wall.end[1] - ny * half_width),
        (wall.start[0] - nx * half_width, wall.start[1] - ny * half_width),
    ]
    return tuple(sorted(_corner_key(point) for point in corners))


def _corner_key(point: tuple[float, float]) -> tuple[int, int]:
    return (
        round(float(point[0]) / SMALL_WALL_CORNER_TOLERANCE),
        round(float(point[1]) / SMALL_WALL_CORNER_TOLERANCE),
    )


def _choose_small_wall_block(a: Wall, b: Wall, walls: list[Wall]) -> tuple[Wall, Wall]:
    a_score = _small_wall_direction_score(a, walls)
    b_score = _small_wall_direction_score(b, walls)
    if b_score > a_score:
        return b, a
    return a, b


def _small_wall_direction_score(wall: Wall, walls: list[Wall]) -> tuple[float, bool, float, float]:
    return (
        _same_direction_neighbor_score(wall, walls),
        float(wall.length) >= float(wall.normalized_width),
        float(wall.confidence),
        float(wall.length),
    )


def _same_direction_neighbor_score(wall: Wall, walls: list[Wall]) -> float:
    orientation = wall_orientation(wall)
    if orientation == "OTHER":
        return 0.0
    score = 0.0
    for other in walls:
        if other is wall or other.id == wall.id:
            continue
        if wall_orientation(other) != orientation:
            continue
        if max(float(other.length), float(other.normalized_width)) <= SMALL_WALL_BLOCK_MAX_SIZE:
            continue
        gap = wall_axis_gap(wall, other, orientation)
        if gap is None or gap > 1500:
            continue
        score += 1.0 + min(float(other.length) / 5000.0, 1.0) + max(0.0, (1500.0 - gap) / 1500.0)
    return round(score, 6)


def wall_orientation(wall: Wall) -> str:
    dx = abs(float(wall.end[0]) - float(wall.start[0]))
    dy = abs(float(wall.end[1]) - float(wall.start[1]))
    if dx >= dy * 5:
        return "H"
    if dy >= dx * 5:
        return "V"
    return "OTHER"


def wall_axis_gap(wall: Wall, other: Wall, orientation: str) -> float | None:
    if orientation == "H":
        wall_y = (float(wall.start[1]) + float(wall.end[1])) / 2.0
        other_y = (float(other.start[1]) + float(other.end[1])) / 2.0
        tolerance = max(120.0, (float(wall.normalized_width) + float(other.normalized_width)) / 2.0 + 80.0)
        if abs(wall_y - other_y) > tolerance:
            return None
        return interval_gap(wall.start[0], wall.end[0], other.start[0], other.end[0])
    if orientation == "V":
        wall_x = (float(wall.start[0]) + float(wall.end[0])) / 2.0
        other_x = (float(other.start[0]) + float(other.end[0])) / 2.0
        tolerance = max(120.0, (float(wall.normalized_width) + float(other.normalized_width)) / 2.0 + 80.0)
        if abs(wall_x - other_x) > tolerance:
            return None
        return interval_gap(wall.start[1], wall.end[1], other.start[1], other.end[1])
    return None


def interval_gap(a1: float, a2: float, b1: float, b2: float) -> float:
    a_min, a_max = sorted([float(a1), float(a2)])
    b_min, b_max = sorted([float(b1), float(b2)])
    if a_max < b_min:
        return b_min - a_max
    if b_max < a_min:
        return a_min - b_max
    return 0.0


def wall_key(wall: Wall) -> tuple:
    start = rounded_key_point(wall.start)
    end = rounded_key_point(wall.end)
    a, b = sorted([start, end])
    return (a, b, round(float(wall.normalized_width), 1))


def rounded_key_point(point: tuple[float, float]) -> tuple[float, float]:
    return (round(float(point[0]), 3), round(float(point[1]), 3))


def _make_wall(
    existing: list[Wall],
    start: tuple[float, float],
    end: tuple[float, float],
    raw_width: float,
    layers: list[str],
    tolerance: float,
    length: float,
    source: str,
) -> Wall:
    normalized, ok, delta = nearest_standard(raw_width, DEFAULT_WALL_WIDTHS, tolerance)
    status = "standard" if delta < 1e-6 else ("auto_normalized" if ok else "nonstandard")
    base_confidence = 0.95 if status in {"standard", "auto_normalized"} else 0.75
    confidence = base_confidence if source == "paired_wall_lines" else max(0.62, base_confidence - 0.12)
    return Wall(
        id=f"W{len(existing) + 1:04d}",
        start=round_point(start),
        end=round_point(end),
        length=round(length, 3),
        raw_width=round(raw_width, 3),
        normalized_width=round(normalized, 3),
        width_status=status,
        recognition_source=source,
        source_layers=sorted(set(layers)),
        confidence=round(confidence, 3),
    )


def _is_t_wall_pier(
    start: tuple[float, float],
    end: tuple[float, float],
    width: float,
    walls: list[Wall],
) -> bool:
    if not walls:
        return False

    pier = Segment("pier", "wall", start, end, "wall")
    pier_direction = unit_direction(pier)
    for wall in walls:
        host = Segment(wall.id, "wall", wall.start, wall.end, "wall")
        host_direction = unit_direction(host)
        if abs(dot(pier_direction, host_direction)) > 0.25:
            continue
        connection_tolerance = max(90.0, width / 2 + wall.normalized_width / 2 + 40)
        if (
            point_to_axis_distance(start, host) <= connection_tolerance
            or point_to_axis_distance(end, host) <= connection_tolerance
        ):
            return True
    return False


def _add_width_issue(issues: list[Issue], wall: Wall, tolerance: float) -> None:
    if wall.width_status == "auto_normalized":
        issues.append(
            Issue(
                wall.id,
                "info",
                f"Wall width normalized from {wall.raw_width} to {wall.normalized_width}.",
                wall.raw_width,
                wall.normalized_width,
            )
        )
    elif wall.width_status == "nonstandard":
        issues.append(
            Issue(
                wall.id,
                "warning",
                f"Wall width {wall.raw_width} is not within {tolerance} of a standard width.",
                wall.raw_width,
                wall.normalized_width,
            )
        )


def recognize_axes(segments: list[Segment], texts: list[CadEntity]) -> list[Axis]:
    axis_segments = [s for s in segments if AXIS_LAYER_RE.search(s.layer) and s.length >= 800]
    axes: list[Axis] = []
    for seg in axis_segments:
        name = _nearest_axis_text(seg, texts)
        axes.append(Axis(f"A{len(axes) + 1:04d}", round_point(seg.start), round_point(seg.end), name, seg.layer, 0.9))
    return axes


def _nearest_axis_text(seg: Segment, texts: list[CadEntity], max_distance: float = 600) -> str | None:
    best_text = None
    best_dist = max_distance
    for ent in texts:
        text = str(ent.data.get("text", "")).strip()
        if not text or len(text) > 8:
            continue
        point = tuple(ent.data.get("point", (0.0, 0.0)))
        d = point_to_axis_distance(point, seg)
        if d < best_dist:
            best_dist = d
            best_text = text
    return best_text


def recognize_openings(
    segments: list[Segment],
    inserts: list[CadEntity],
    walls: list[Wall],
    entities: list[CadEntity],
) -> list[Opening]:
    openings: list[Opening] = []
    used_segments: set[str] = set()

    recognize_polyline_opening_shapes(entities, walls, openings)
    recognize_sliding_door_rectangles(segments, walls, openings, used_segments)
    recognize_parallel_opening_patterns(segments, walls, openings, used_segments)
    recognize_door_arcs(entities, walls, openings)

    for seg in segments:
        if seg.id in used_segments:
            continue
        kind = None
        if DOOR_LAYER_RE.search(seg.layer):
            kind = "door"
        elif WINDOW_LAYER_RE.search(seg.layer):
            kind = "window"
        if kind is None:
            continue
        width = seg.length if seg.length > 1 else None
        if width is not None and (width < MIN_OPENING_WIDTH or width > MAX_OPENING_WIDTH):
            continue
        duplicate_minimum = 750 if kind == "door" else 450
        if is_near_existing_opening(seg.midpoint, openings, width, minimum=duplicate_minimum):
            continue
        host_id, host_dist = _nearest_wall(seg.midpoint, walls)
        openings.append(
            Opening(
                id=f"O{len(openings) + 1:04d}",
                kind=kind,
                point=round_point(seg.midpoint),
                width=round(width, 3) if width else None,
                layer=seg.layer,
                block_name=None,
                host_wall_id=host_id,
                confidence=0.88 if host_dist is not None and host_dist < 600 else 0.65,
                component_category=opening_component_category(kind, "layer_line"),
                source="layer_line",
            )
        )

    for ent in inserts:
        layer = ent.layer
        name = str(ent.data.get("name", ""))
        kind = opening_kind_from_block(ent)
        if kind is None:
            continue
        point = tuple(ent.data.get("point", (0.0, 0.0)))
        block_geometry = block_door_geometry(ent, walls) if kind == "door" else None
        if block_geometry is not None:
            point = tuple(block_geometry.point)
        if is_near_existing_opening(point, openings, None):
            continue
        panel_evidence = None
        if block_geometry is not None and block_geometry.panel_start and block_geometry.panel_end:
            panel_evidence = {
                "panel_start": block_geometry.panel_start,
                "panel_end": block_geometry.panel_end,
            }
        host_id, host_dist = nearest_door_host(point, walls, panel_evidence)
        if host_dist is None or host_dist > 1200:
            host_id = None
        mark_dimensions = extract_mark_dimensions(name, kind_hint=kind, allow_unprefixed=True)
        block_width = opening_width_from_block_bounds(ent)
        width = mark_dimensions["width_mm"] if mark_dimensions else block_width
        height = mark_dimensions["height_mm"] if mark_dimensions else None
        annotation = mark_dimensions["text"] if mark_dimensions else None
        size_source = "block_name_mark" if mark_dimensions else "block_geometry_bounds" if block_width else None
        openings.append(
            Opening(
                id=f"O{len(openings) + 1:04d}",
                kind=kind,
                point=round_point(point),
                width=width,
                layer=layer,
                block_name=name,
                host_wall_id=host_id,
                confidence=0.9 if host_dist is not None and host_dist < 700 else 0.72,
                height_mm=height,
                annotation=annotation,
                open_direction=block_geometry.open_direction if block_geometry else None,
                swing_side=block_geometry.swing_side if block_geometry else None,
                component_category=opening_category_from_block(kind, name),
                sill_height_mm=0 if kind == "door" else None,
                source="block",
                height_source="block_name_mark" if height is not None else None,
                width_source=size_source,
                annotation_source="block_name" if annotation else None,
                size_source=size_source,
                panel_start=block_geometry.panel_start if block_geometry else None,
                panel_end=block_geometry.panel_end if block_geometry else None,
                panel_thickness_mm=block_geometry.panel_thickness_mm if block_geometry else None,
                panel_wall_angle_deg=block_geometry.panel_wall_angle_deg if block_geometry else None,
                swing_source=block_geometry.swing_source if block_geometry else None,
                swing_confidence=block_geometry.swing_confidence if block_geometry else None,
            )
        )
    recognize_sliding_window_patterns(segments, walls, openings)
    openings = merge_adjacent_door_frames(openings, walls)
    return openings


def block_door_geometry(ent: CadEntity, walls: list[Wall]) -> Opening | None:
    children: list[CadEntity] = []
    for item in ent.data.get("block_entities", []):
        if not isinstance(item, dict):
            continue
        entity_type = str(item.get("type", "")).upper()
        data = item.get("data")
        if entity_type and isinstance(data, dict):
            children.append(CadEntity(entity_type, str(item.get("layer", ent.layer)), data))
    if not children:
        return None
    candidates: list[Opening] = []
    recognize_door_arcs(children, walls, candidates)
    if not candidates:
        return None
    block_point = tuple(ent.data.get("point", (0.0, 0.0)))
    return min(candidates, key=lambda opening: distance(tuple(opening.point), block_point))


def recognize_sliding_door_rectangles(
    segments: list[Segment],
    walls: list[Wall],
    openings: list[Opening],
    used_segments: set[str],
) -> None:
    rects = line_rectangles(
        [
            seg
            for seg in segments
            if seg.id not in used_segments and not WALL_LAYER_RE.search(seg.layer) and not AXIS_LAYER_RE.search(seg.layer)
        ]
    )
    narrow = [
        rect
        for rect in rects
        if 600 <= max(rect["width"], rect["height"]) <= 2200
        and 25 <= min(rect["width"], rect["height"]) <= 180
    ]
    used_rects: set[int] = set()
    for i, a in enumerate(narrow):
        if i in used_rects:
            continue
        for j, b in enumerate(narrow[i + 1 :], start=i + 1):
            if j in used_rects:
                continue
            merged = sliding_door_pair(a, b)
            if merged is None:
                continue
            center = merged["center"]
            if is_near_existing_opening(center, openings, merged["width"], minimum=800):
                continue
            host_id, host_dist = _nearest_wall(center, walls)
            openings.append(
                Opening(
                    id=f"O{len(openings) + 1:04d}",
                    kind="door",
                    point=round_point(center),
                    width=round(merged["width"], 3),
                    layer=";".join(sorted(set(a["layers"] + b["layers"]))),
                    block_name=None,
                    host_wall_id=host_id,
                    confidence=0.84 if host_dist is not None and host_dist < 700 else 0.66,
                    open_direction="sliding",
                    swing_side="double",
                    component_category="sliding_door",
                    sill_height_mm=0,
                    source="sliding_door_double_rectangles",
                )
            )
            used_segments.update(a["segment_ids"] + b["segment_ids"])
            used_rects.update({i, j})
            break


def recognize_polyline_opening_shapes(entities: list[CadEntity], walls: list[Wall], openings: list[Opening]) -> None:
    for ent in entities:
        if ent.type not in {"LWPOLYLINE", "POLYLINE"} or not ent.data.get("closed"):
            continue
        kind = opening_kind_from_text(ent.layer)
        if kind is None:
            continue
        points = [tuple(p) for p in ent.data.get("points", [])]
        if len(points) < 4:
            continue
        min_x, min_y, max_x, max_y = bbox(points)
        width = max_x - min_x
        height = max_y - min_y
        long_side = max(width, height)
        short_side = min(width, height)
        if long_side < MIN_OPENING_WIDTH or long_side > MAX_OPENING_WIDTH:
            continue
        if short_side <= 0 or short_side > 900:
            continue
        box_area = width * height
        if box_area <= 0:
            continue
        point = centroid(points)
        if is_near_existing_opening(point, openings, long_side):
            continue
        host_id, host_dist = _nearest_wall(point, walls)
        openings.append(
            Opening(
                id=f"O{len(openings) + 1:04d}",
                kind=kind,
                point=round_point(point),
                width=round(long_side, 3),
                layer=ent.layer,
                block_name=None,
                host_wall_id=host_id,
                confidence=0.86 if host_dist is not None and host_dist < 700 else 0.68,
                component_category=opening_component_category(kind, "layer_closed_rectangle"),
                source="layer_closed_rectangle",
            )
        )


def recognize_parallel_opening_patterns(
    segments: list[Segment],
    walls: list[Wall],
    openings: list[Opening],
    used_segments: set[str],
) -> None:
    for kind in ["door", "window"]:
        candidates = [
            seg
            for seg in segments
            if opening_kind_from_text(seg.layer) == kind
            and seg.orientation in {"H", "V"}
            and MIN_OPENING_WIDTH <= seg.length <= MAX_OPENING_WIDTH
            and seg.id not in used_segments
        ]
        for orientation in ["H", "V"]:
            oriented = [seg for seg in candidates if seg.orientation == orientation]
            oriented.sort(key=lambda seg: (parallel_position(seg), perpendicular_position(seg)))
            for seg in oriented:
                if seg.id in used_segments:
                    continue
                cluster = [
                    other
                    for other in oriented
                    if other.id not in used_segments
                    and abs(parallel_position(other) - parallel_position(seg)) <= 90
                    and abs(other.length - seg.length) <= max(160, seg.length * 0.22)
                    and 0 <= abs(perpendicular_position(other) - perpendicular_position(seg)) <= 700
                ]
                cluster = sorted(cluster, key=perpendicular_position)
                if len(cluster) < 2:
                    continue
                if kind == "window" and len(cluster) < 3:
                    continue
                if len(cluster) > 6:
                    cluster = cluster[:6]
                span = perpendicular_position(cluster[-1]) - perpendicular_position(cluster[0])
                if span < 20 or span > 700:
                    continue
                center = cluster_center(cluster)
                width = sum(item.length for item in cluster) / len(cluster)
                if is_near_existing_opening(center, openings, width):
                    continue
                host_id, host_dist = _nearest_wall(center, walls)
                source = "parallel_door_lines" if kind == "door" else "parallel_window_lines"
                openings.append(
                    Opening(
                        id=f"O{len(openings) + 1:04d}",
                        kind=kind,
                        point=round_point(center),
                        width=round(width, 3),
                        layer=";".join(sorted(set(item.layer for item in cluster))),
                        block_name=None,
                        host_wall_id=host_id,
                        confidence=0.8 if host_dist is not None and host_dist < 700 else 0.62,
                        sill_height_mm=0 if kind == "door" else None,
                        component_category=opening_component_category(kind, source),
                        source=source,
                    )
                )
                used_segments.update(item.id for item in cluster)


def recognize_sliding_window_patterns(segments: list[Segment], walls: list[Wall], openings: list[Opening]) -> None:
    candidates = [
        seg
        for seg in segments
        if seg.orientation in {"H", "V"}
        and 400 <= seg.length <= 3000
        and not WALL_LAYER_RE.search(seg.layer)
        and not AXIS_LAYER_RE.search(seg.layer)
        and not DOOR_LAYER_RE.search(seg.layer)
    ]
    for orientation in ["H", "V"]:
        group = [seg for seg in candidates if seg.orientation == orientation]
        group.sort(key=lambda seg: (parallel_position(seg), perpendicular_position(seg)))
        used: set[str] = set()
        for seg in group:
            if seg.id in used:
                continue
            nearby = [
                other
                for other in group
                if other.id not in used
                and abs(parallel_position(other) - parallel_position(seg)) <= 80
                and abs(other.length - seg.length) <= max(120, seg.length * 0.18)
                and 15 <= abs(perpendicular_position(other) - perpendicular_position(seg)) <= 520
            ]
            cluster = sorted([seg] + nearby, key=perpendicular_position)[:5]
            if len(cluster) != 5:
                continue
            span = perpendicular_position(cluster[-1]) - perpendicular_position(cluster[0])
            if span < 60 or span > 650:
                continue
            center = cluster_center(cluster)
            if any(distance(center, tuple(opening.point)) < 500 for opening in openings):
                continue
            host_id, host_dist = _nearest_wall(center, walls)
            openings.append(
                Opening(
                    id=f"O{len(openings) + 1:04d}",
                    kind="window",
                    point=round_point(center),
                    width=round(sum(s.length for s in cluster) / len(cluster), 3),
                    layer=";".join(sorted(set(s.layer for s in cluster))),
                    block_name=None,
                    host_wall_id=host_id,
                    confidence=0.72 if host_dist is not None and host_dist < 700 else 0.52,
                    component_category="sliding_window",
                    source="five_parallel_lines",
                )
            )
            used.update(s.id for s in cluster)


def parallel_position(seg: Segment) -> float:
    return seg.midpoint[0] if seg.orientation == "H" else seg.midpoint[1]


def perpendicular_position(seg: Segment) -> float:
    return seg.midpoint[1] if seg.orientation == "H" else seg.midpoint[0]


def cluster_center(cluster: list[Segment]) -> tuple[float, float]:
    points = [seg.midpoint for seg in cluster]
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def line_rectangles(segments: list[Segment]) -> list[dict]:
    horizontals = [seg for seg in segments if seg.orientation == "H"]
    verticals = [seg for seg in segments if seg.orientation == "V"]
    rects: list[dict] = []
    for left_index, left in enumerate(verticals):
        for right in verticals[left_index + 1 :]:
            min_x, max_x = sorted([left.start[0], right.start[0]])
            min_y_left, max_y_left = sorted([left.start[1], left.end[1]])
            min_y_right, max_y_right = sorted([right.start[1], right.end[1]])
            if abs(min_y_left - min_y_right) > 8 or abs(max_y_left - max_y_right) > 8:
                continue
            bottom = matching_line_horizontal(horizontals, min_x, max_x, min_y_left)
            top = matching_line_horizontal(horizontals, min_x, max_x, max_y_left)
            if bottom is None or top is None:
                continue
            width = max_x - min_x
            height = max_y_left - min_y_left
            if width <= 0 or height <= 0:
                continue
            rects.append(
                {
                    "min_x": min_x,
                    "min_y": min_y_left,
                    "max_x": max_x,
                    "max_y": max_y_left,
                    "width": width,
                    "height": height,
                    "center": ((min_x + max_x) / 2, (min_y_left + max_y_left) / 2),
                    "layers": [left.layer, right.layer, bottom.layer, top.layer],
                    "segment_ids": [left.id, right.id, bottom.id, top.id],
                }
            )
    return rects


def matching_line_horizontal(horizontals: list[Segment], min_x: float, max_x: float, y: float) -> Segment | None:
    for seg in horizontals:
        h_min_x, h_max_x = sorted([seg.start[0], seg.end[0]])
        if abs(seg.start[1] - y) <= 8 and abs(h_min_x - min_x) <= 8 and abs(h_max_x - max_x) <= 8:
            return seg
    return None


def fragmented_line_rectangles(segments: list[Segment], tolerance: float = 8.0) -> list[dict]:
    horizontals = [seg for seg in segments if seg.orientation == "H"]
    verticals = [seg for seg in segments if seg.orientation == "V"]
    xs = sorted({round(coord, 3) for seg in verticals for coord in [seg.start[0], seg.end[0]]})
    ys = sorted({round(coord, 3) for seg in horizontals for coord in [seg.start[1], seg.end[1]]})
    rects: list[dict] = []
    seen: set[tuple[float, float, float, float]] = set()

    for i, min_x in enumerate(xs):
        for max_x in xs[i + 1 :]:
            width = max_x - min_x
            if not valid_column_size(width, width):
                continue
            for j, min_y in enumerate(ys):
                for max_y in ys[j + 1 :]:
                    height = max_y - min_y
                    if not valid_column_size(width, height):
                        continue
                    left = side_segments_cover(verticals, min_x, min_y, max_y, "V", tolerance)
                    right = side_segments_cover(verticals, max_x, min_y, max_y, "V", tolerance)
                    bottom = side_segments_cover(horizontals, min_y, min_x, max_x, "H", tolerance)
                    top = side_segments_cover(horizontals, max_y, min_x, max_x, "H", tolerance)
                    if not (left and right and bottom and top):
                        continue
                    key = (round(min_x, 3), round(min_y, 3), round(max_x, 3), round(max_y, 3))
                    if key in seen:
                        continue
                    seen.add(key)
                    side_segments = left + right + bottom + top
                    rects.append(
                        {
                            "min_x": min_x,
                            "min_y": min_y,
                            "max_x": max_x,
                            "max_y": max_y,
                            "width": width,
                            "height": height,
                            "center": ((min_x + max_x) / 2, (min_y + max_y) / 2),
                            "layers": [seg.layer for seg in side_segments],
                            "segment_ids": [seg.id for seg in side_segments],
                        }
                    )
    return rects


def side_segments_cover(
    segments: list[Segment],
    fixed_coord: float,
    span_start: float,
    span_end: float,
    orientation: str,
    tolerance: float,
) -> list[Segment]:
    pieces: list[tuple[float, float, Segment]] = []
    for seg in segments:
        if orientation == "V":
            coord = seg.start[0]
            lo, hi = sorted([seg.start[1], seg.end[1]])
        else:
            coord = seg.start[1]
            lo, hi = sorted([seg.start[0], seg.end[0]])
        if abs(coord - fixed_coord) > tolerance:
            continue
        overlap_lo = max(lo, span_start)
        overlap_hi = min(hi, span_end)
        if overlap_hi - overlap_lo <= tolerance:
            continue
        pieces.append((overlap_lo, overlap_hi, seg))

    if not pieces:
        return []
    pieces.sort(key=lambda item: item[0])
    covered_start = span_start
    used: list[Segment] = []
    for lo, hi, seg in pieces:
        if lo > covered_start + tolerance:
            continue
        if hi > covered_start:
            covered_start = hi
            used.append(seg)
        if covered_start >= span_end - tolerance:
            return used
    return []


def sliding_door_pair(a: dict, b: dict) -> dict | None:
    if not same_sliding_orientation(a, b):
        return None
    if a["width"] >= a["height"]:
        overlap = interval_overlap(a["min_x"], a["max_x"], b["min_x"], b["max_x"])
        gap = interval_gap(a["min_x"], a["max_x"], b["min_x"], b["max_x"])
        offset = abs(a["center"][1] - b["center"][1])
        length = (a["width"] + b["width"]) / 2
        if (overlap < length * 0.15 and gap > 80) or offset > 180:
            return None
    else:
        overlap = interval_overlap(a["min_y"], a["max_y"], b["min_y"], b["max_y"])
        gap = interval_gap(a["min_y"], a["max_y"], b["min_y"], b["max_y"])
        offset = abs(a["center"][0] - b["center"][0])
        length = (a["height"] + b["height"]) / 2
        if (overlap < length * 0.15 and gap > 80) or offset > 180:
            return None
    min_x = min(a["min_x"], b["min_x"])
    max_x = max(a["max_x"], b["max_x"])
    min_y = min(a["min_y"], b["min_y"])
    max_y = max(a["max_y"], b["max_y"])
    return {
        "center": ((min_x + max_x) / 2, (min_y + max_y) / 2),
        "width": max(max_x - min_x, max_y - min_y),
    }


def same_sliding_orientation(a: dict, b: dict) -> bool:
    return (a["width"] >= a["height"]) == (b["width"] >= b["height"])


def interval_overlap(a1: float, a2: float, b1: float, b2: float) -> float:
    lo = max(min(a1, a2), min(b1, b2))
    hi = min(max(a1, a2), max(b1, b2))
    return max(0.0, hi - lo)


def interval_gap(a1: float, a2: float, b1: float, b2: float) -> float:
    if max(a1, a2) < min(b1, b2):
        return min(b1, b2) - max(a1, a2)
    if max(b1, b2) < min(a1, a2):
        return min(a1, a2) - max(b1, b2)
    return 0.0


def opening_component_category(kind: str, source: str | None) -> str:
    if kind == "door":
        if source == "quarter_arc":
            return "single_swing_door"
        if source == "double_swing_arc":
            return "double_swing_door"
        if source in {"sliding_door_double_rectangles"}:
            return "sliding_door"
        return "unknown"
    if kind == "window":
        if source in {"five_parallel_lines"}:
            return "sliding_window"
        return "unknown"
    return "unknown"


def merge_adjacent_door_frames(openings: list[Opening], walls: list[Wall]) -> list[Opening]:
    result: list[Opening] = []
    used: set[int] = set()
    for i, opening in enumerate(openings):
        if i in used:
            continue
        if not mergeable_door_frame(opening):
            result.append(opening)
            continue
        group = [i]
        changed = True
        while changed:
            changed = False
            for j, other in enumerate(openings):
                if j in used or j in group or not mergeable_door_frame(other):
                    continue
                if any(adjacent_door_frames(openings[index], other, walls) for index in group):
                    group.append(j)
                    changed = True
        if len(group) < 2:
            result.append(opening)
            continue
        merged = merge_door_frame_group([openings[index] for index in group], walls)
        result.append(merged)
        used.update(group)

    for index, opening in enumerate(result, start=1):
        opening.id = f"O{index:04d}"
    return result


def mergeable_door_frame(opening: Opening) -> bool:
    if opening.kind != "door":
        return False
    if opening.component_category == "sliding_door":
        return False
    return opening.source in {
        "quarter_arc",
        "layer_line",
        "parallel_door_lines",
        "layer_closed_rectangle",
    }


def adjacent_door_frames(a: Opening, b: Opening, walls: list[Wall]) -> bool:
    pa = tuple(a.point)
    pb = tuple(b.point)
    center_distance = distance(pa, pb)
    width_a = float(a.width or 0)
    width_b = float(b.width or 0)
    max_width = max(width_a, width_b, 1.0)
    if center_distance < 1 or center_distance > max(1200.0, max_width * 1.35):
        return False
    same_axis = roughly_same_axis(pa, pb, max(220.0, min(max_width * 0.35, 450.0)))
    if not same_axis and not staggered_sliding_door_pair(pa, pb, width_a, width_b):
        return False
    if wall_between_points(pa, pb, walls, ignore_ids={a.host_wall_id, b.host_wall_id}):
        return False
    return True


def roughly_same_axis(a: tuple[float, float], b: tuple[float, float], tolerance: float) -> bool:
    return abs(a[0] - b[0]) <= tolerance or abs(a[1] - b[1]) <= tolerance


def staggered_sliding_door_pair(
    a: tuple[float, float],
    b: tuple[float, float],
    width_a: float,
    width_b: float,
) -> bool:
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    long_delta = max(dx, dy)
    short_delta = min(dx, dy)
    average_width = max((width_a + width_b) / 2, 1.0)
    total_width = width_a + width_b
    return (
        0.75 * average_width <= long_delta <= 1.75 * average_width
        and short_delta <= max(420.0, average_width * 0.65)
        and 900.0 <= total_width <= 2600.0
    )


def wall_between_points(
    a: tuple[float, float],
    b: tuple[float, float],
    walls: list[Wall],
    ignore_ids: set[str | None] | None = None,
) -> bool:
    ignore_ids = ignore_ids or set()
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    connector = Segment("door-gap", "", a, b, "")
    connector_length = connector.length
    if connector_length <= 0:
        return False
    for wall in walls:
        if wall.id in ignore_ids:
            continue
        wall_segment = Segment(wall.id, "", wall.start, wall.end, "")
        d = point_to_axis_distance(mid, wall_segment)
        if d > max(120.0, float(wall.normalized_width) / 2 + 80.0):
            continue
        wall_mid = wall_segment.midpoint
        if point_to_axis_distance(wall_mid, connector) <= max(120.0, float(wall.normalized_width) / 2 + 80.0):
            return True
    return False


def merge_door_frame_group(group: list[Opening], walls: list[Wall]) -> Opening:
    points = [tuple(opening.point) for opening in group]
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    center = ((min_x + max_x) / 2, (min_y + max_y) / 2)
    span = max(max_x - min_x, max_y - min_y)
    average_width = sum(float(opening.width or 0) for opening in group) / len(group)
    width = span + average_width
    host_id, host_dist = _nearest_wall(center, walls)
    confidence_values = [opening.confidence for opening in group if opening.confidence is not None]
    return Opening(
        id="",
        kind="door",
        point=round_point(center),
        width=round(width, 3),
        layer=";".join(sorted(set(opening.layer for opening in group if opening.layer))),
        block_name=None,
        host_wall_id=host_id,
        confidence=round(min(confidence_values) if confidence_values else 0.66, 3),
        open_direction="sliding",
        swing_side="double",
        component_category="sliding_door",
        sill_height_mm=0,
        source="merged_adjacent_door_frames",
    )


def recognize_door_arcs(entities: list[CadEntity], walls: list[Wall], openings: list[Opening]) -> None:
    candidates: list[tuple[int, CadEntity, float, tuple[float, float], str | None, float | None]] = []
    for index, ent in enumerate(entities):
        if ent.type != "ARC":
            continue
        radius = float(ent.data.get("radius", 0) or 0)
        if radius < 600 or radius > 1600:
            continue
        sweep = arc_sweep(ent)
        if sweep < 65 or sweep > 115:
            continue
        center = tuple(ent.data.get("center", (0.0, 0.0)))
        raw_panel = door_panel_geometry(ent, center, radius, entities, None, walls)
        host_id, host_dist = nearest_door_host(center, walls, raw_panel)
        candidates.append((index, ent, radius, center, host_id, host_dist))

    used: set[int] = set()
    for i, (index_a, ent_a, radius_a, center_a, host_a, dist_a) in enumerate(candidates):
        if index_a in used:
            continue
        for index_b, ent_b, radius_b, center_b, host_b, dist_b in candidates[i + 1 :]:
            if index_b in used:
                continue
            if host_a is not None and host_b is not None and host_a != host_b:
                continue
            if abs(radius_a - radius_b) > 180:
                continue
            center_distance = distance(center_a, center_b)
            average_radius = (radius_a + radius_b) / 2
            if not average_radius * 1.35 <= center_distance <= average_radius * 2.65:
                continue
            point = ((center_a[0] + center_b[0]) / 2, (center_a[1] + center_b[1]) / 2)
            raw_panel_a = door_panel_geometry(ent_a, center_a, radius_a, entities, None, walls)
            raw_panel_b = door_panel_geometry(ent_b, center_b, radius_b, entities, None, walls)
            host_id, host_dist = nearest_door_host(point, walls, raw_panel_a or raw_panel_b)
            panel_a = door_panel_geometry(ent_a, center_a, radius_a, entities, host_id, walls)
            panel_b = door_panel_geometry(ent_b, center_b, radius_b, entities, host_id, walls)
            panels = [panel for panel in (panel_a, panel_b) if panel]
            for panel in panels:
                remove_panel_line_false_openings(openings, panel)
            if is_near_existing_opening(point, openings, center_distance):
                continue
            panel_direction = next(
                (
                    panel["open_direction"] for panel in panels
                    if all(other["open_direction"] == panel["open_direction"] for other in panels)
                ),
                None,
            )
            openings.append(
                Opening(
                    id=f"O{len(openings) + 1:04d}",
                    kind="door",
                    point=round_point(point),
                    width=round(center_distance, 3),
                    layer=";".join(sorted({ent_a.layer, ent_b.layer})),
                    block_name=None,
                    host_wall_id=host_id,
                    confidence=0.82 if host_dist is not None and host_dist < 900 else 0.62,
                    open_direction=panel_direction or arc_open_direction(ent_a),
                    swing_side="double",
                    component_category="double_swing_door",
                    sill_height_mm=0,
                    source="double_swing_arc",
                    panel_start=panels[0]["panel_start"] if panels else None,
                    panel_end=panels[0]["panel_end"] if panels else None,
                    panel_thickness_mm=max(
                        (float(panel["panel_thickness_mm"]) for panel in panels if panel.get("panel_thickness_mm") is not None),
                        default=None,
                    ),
                    panel_wall_angle_deg=min(
                        (float(panel["panel_wall_angle_deg"]) for panel in panels),
                        default=None,
                    ),
                    swing_source="cad_door_panel_geometry" if panels else "cad_swing_arc",
                    swing_confidence=0.98 if panels else 0.82,
                )
            )
            used.update({index_a, index_b})
            break

    for index, ent, radius, center, host_id, host_dist in candidates:
        if index in used:
            continue
        raw_panel = door_panel_geometry(ent, center, radius, entities, None, walls)
        host_id, host_dist = nearest_door_host(center, walls, raw_panel)
        point = door_opening_center_from_arc(ent, center, radius, host_id, walls)
        host_id, host_dist = nearest_door_host(point, walls, raw_panel)
        panel = door_panel_geometry(ent, center, radius, entities, host_id, walls)
        if panel:
            remove_panel_line_false_openings(openings, panel)
        if is_near_existing_opening(point, openings, radius):
            continue
        openings.append(
            Opening(
                id=f"O{len(openings) + 1:04d}",
                kind="door",
                point=round_point(point),
                width=round(radius, 3),
                layer=ent.layer,
                block_name=None,
                host_wall_id=host_id,
                confidence=0.78 if host_dist is not None and host_dist < 900 else 0.58,
                open_direction=panel["open_direction"] if panel else arc_open_direction(ent),
                swing_side=arc_swing_side(ent, center, point, host_id, walls),
                component_category="single_swing_door",
                sill_height_mm=0,
                source="quarter_arc",
                panel_start=panel["panel_start"] if panel else None,
                panel_end=panel["panel_end"] if panel else None,
                panel_thickness_mm=panel["panel_thickness_mm"] if panel else None,
                panel_wall_angle_deg=panel["panel_wall_angle_deg"] if panel else None,
                swing_source="cad_door_panel_geometry" if panel else "cad_swing_arc",
                swing_confidence=0.98 if panel else 0.78,
            )
        )


def door_opening_center_from_arc(
    ent: CadEntity,
    hinge: tuple[float, float],
    radius: float,
    host_id: str | None,
    walls: list[Wall],
) -> tuple[float, float]:
    endpoint = arc_endpoint_on_host_wall(ent, hinge, radius, host_id, walls)
    if endpoint is None:
        endpoints = arc_endpoints(ent, hinge, radius)
        endpoint = max(endpoints, key=lambda pt: distance(pt, hinge))
    return ((hinge[0] + endpoint[0]) / 2, (hinge[1] + endpoint[1]) / 2)


def arc_endpoint_on_host_wall(
    ent: CadEntity,
    hinge: tuple[float, float],
    radius: float,
    host_id: str | None,
    walls: list[Wall],
) -> tuple[float, float] | None:
    endpoints = arc_endpoints(ent, hinge, radius)
    host = next((wall for wall in walls if wall.id == host_id), None)
    if host is None:
        return None
    dx = host.end[0] - host.start[0]
    dy = host.end[1] - host.start[1]
    host_length = (dx * dx + dy * dy) ** 0.5
    if host_length <= 0:
        return None
    return min(
        endpoints,
        key=lambda pt: abs((pt[0] - host.start[0]) * dy - (pt[1] - host.start[1]) * dx) / host_length,
    )


def arc_endpoints(ent: CadEntity, center: tuple[float, float], radius: float) -> list[tuple[float, float]]:
    start = radians(float(ent.data.get("start_angle", 0) or 0))
    end = radians(float(ent.data.get("end_angle", 0) or 0))
    return [
        (center[0] + cos(start) * radius, center[1] + sin(start) * radius),
        (center[0] + cos(end) * radius, center[1] + sin(end) * radius),
    ]


def door_panel_geometry(
    arc: CadEntity,
    hinge: tuple[float, float],
    radius: float,
    entities: list[CadEntity],
    host_id: str | None,
    walls: list[Wall],
) -> dict | None:
    host = next((wall for wall in walls if wall.id == host_id), None)
    host_direction = (
        unit_direction(Segment(host.id, "wall", host.start, host.end, "wall"))
        if host is not None
        else None
    )
    arc_points = arc_endpoints(arc, hinge, radius)
    candidates: list[tuple[Segment, tuple[float, float], tuple[float, float]]] = []
    hinge_tolerance = max(100.0, radius * 0.1)
    endpoint_tolerance = max(140.0, radius * 0.14)
    for index, entity in enumerate(entities):
        if entity.type != "LINE":
            continue
        if entity.layer != arc.layer and not DOOR_LAYER_RE.search(entity.layer or ""):
            continue
        start = tuple(entity.data.get("start", (0.0, 0.0)))
        end = tuple(entity.data.get("end", (0.0, 0.0)))
        segment = Segment(f"door-panel-{index}", entity.layer, start, end, "door_panel")
        if not radius * 0.72 <= segment.length <= radius * 1.15:
            continue
        near, far = (start, end) if distance(start, hinge) <= distance(end, hinge) else (end, start)
        if distance(near, hinge) > hinge_tolerance:
            continue
        if min(distance(far, endpoint) for endpoint in arc_points) > endpoint_tolerance:
            continue
        candidates.append((segment, near, far))
    if not candidates:
        return None

    paired = []
    for i, first in enumerate(candidates):
        first_direction = unit_direction(Segment(first[0].id, first[0].layer, first[1], first[2], "door_panel"))
        for second in candidates[i + 1 :]:
            second_direction = unit_direction(
                Segment(second[0].id, second[0].layer, second[1], second[2], "door_panel")
            )
            if abs(dot(first_direction, second_direction)) < 0.96:
                continue
            separation = point_to_axis_distance(second[0].midpoint, first[0])
            if 2.0 <= separation <= 120.0:
                paired.append((separation, first, second))
    if paired:
        _, first, second = min(
            paired,
            key=lambda item: (
                abs(item[0] - 20.0),
                distance(item[1][1], hinge) + distance(item[2][1], hinge),
            ),
        )
        selected = [first, second]
    else:
        if host_direction is not None:
            candidates = [
                item for item in candidates
                if abs(
                    dot(
                        unit_direction(Segment(item[0].id, item[0].layer, hinge, item[2], "door_panel")),
                        host_direction,
                    )
                ) <= 0.28
            ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: (distance(item[1], hinge), abs(item[0].length - radius)))
        selected = candidates[:1]
    far_x = sum(item[2][0] for item in selected) / len(selected)
    far_y = sum(item[2][1] for item in selected) / len(selected)
    panel_end = (far_x, far_y)
    panel_direction = unit_direction(Segment("door-panel-axis", "", hinge, panel_end, "door_panel"))
    wall_angle = None
    if host_direction is not None:
        absolute_dot = max(-1.0, min(1.0, abs(dot(panel_direction, host_direction))))
        wall_angle = degrees(acos(absolute_dot))
    panel_thickness = None
    if len(selected) >= 2:
        panel_thickness = point_to_axis_distance(selected[1][0].midpoint, selected[0][0])
        if panel_thickness > 120:
            panel_thickness = None
    return {
        "panel_start": round_point(hinge),
        "panel_end": round_point(panel_end),
        "panel_thickness_mm": round(panel_thickness, 3) if panel_thickness is not None else None,
        "panel_wall_angle_deg": round(wall_angle, 3) if wall_angle is not None else None,
        "panel_axis_angle_deg": round(degrees(atan2(panel_direction[1], panel_direction[0])) % 360, 3),
        "open_direction": vector_open_direction(panel_direction),
    }


def nearest_door_host(
    point: tuple[float, float],
    walls: list[Wall],
    panel: dict | None,
) -> tuple[str | None, float | None]:
    if not panel:
        return _nearest_wall(point, walls)
    panel_start = tuple(panel.get("panel_start") or ())
    panel_end = tuple(panel.get("panel_end") or ())
    if len(panel_start) < 2 or len(panel_end) < 2:
        return _nearest_wall(point, walls)
    panel_direction = unit_direction(
        Segment("door-panel-axis", "", panel_start, panel_end, "door_panel")
    )
    compatible = []
    for wall in walls:
        wall_direction = unit_direction(Segment(wall.id, "wall", wall.start, wall.end, "wall"))
        if abs(dot(panel_direction, wall_direction)) <= 0.28:
            compatible.append(wall)
    return _nearest_wall(point, compatible) if compatible else (None, None)


def vector_open_direction(vector: tuple[float, float]) -> str:
    dx, dy = vector
    if abs(dx) >= abs(dy):
        return "east" if dx >= 0 else "west"
    return "north" if dy >= 0 else "south"


def remove_panel_line_false_openings(openings: list[Opening], panel: dict) -> None:
    panel_start = tuple(panel.get("panel_start") or ())
    panel_end = tuple(panel.get("panel_end") or ())
    if len(panel_start) < 2 or len(panel_end) < 2:
        return
    panel_midpoint = (
        (float(panel_start[0]) + float(panel_end[0])) / 2.0,
        (float(panel_start[1]) + float(panel_end[1])) / 2.0,
    )
    openings[:] = [
        opening for opening in openings
        if not (
            opening.kind == "door"
            and opening.source == "parallel_door_lines"
            and distance(tuple(opening.point), panel_midpoint) <= 160.0
        )
    ]


def arc_swing_side(
    ent: CadEntity,
    hinge: tuple[float, float],
    opening_center: tuple[float, float],
    host_id: str | None,
    walls: list[Wall],
) -> str:
    host = next((wall for wall in walls if wall.id == host_id), None)
    if host is not None:
        direction = unit_direction(Segment(host.id, "wall", host.start, host.end, "wall"))
        hinge_vector = (hinge[0] - opening_center[0], hinge[1] - opening_center[1])
        along_wall = dot(direction, hinge_vector)
        if abs(along_wall) > 1e-6:
            return "left" if along_wall < 0 else "right"
        side = cross_2d(direction, hinge_vector)
        if abs(side) > 1e-6:
            return "left" if side > 0 else "right"

    endpoints = arc_endpoints(ent, hinge, float(ent.data.get("radius", 0) or 0))
    door_vector = (opening_center[0] - hinge[0], opening_center[1] - hinge[1])
    arc_vector = (endpoints[1][0] - endpoints[0][0], endpoints[1][1] - endpoints[0][1])
    side = cross_2d(door_vector, arc_vector)
    if abs(side) <= 1e-6:
        return "unknown"
    return "left" if side > 0 else "right"


def cross_2d(a: tuple[float, float], b: tuple[float, float]) -> float:
    return a[0] * b[1] - a[1] * b[0]


def opening_kind_from_text(text: str) -> str | None:
    if DOOR_LAYER_RE.search(text):
        return "door"
    if WINDOW_LAYER_RE.search(text):
        return "window"
    return None


def opening_kind_from_block(ent: CadEntity) -> str | None:
    name = str(ent.data.get("name", ""))
    layers = " ".join(str(layer) for layer in ent.data.get("block_layers", []))
    haystack = f"{ent.layer} {name} {layers}"
    kind = opening_kind_from_text(haystack)
    if kind is not None:
        return kind
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    opening_layer_context = OPENING_BLOCK_LAYER_RE.search(f"{ent.layer} {layers}") is not None
    if normalized.startswith(("tlm", "door", "men", "slidingdoor", "doubledoor", "singledoor")):
        return "door"
    if normalized.startswith(("tlc", "pkc", "window", "win", "slidingwindow", "casementwindow")):
        return "window"
    if extract_mark_dimensions(name, kind_hint="door"):
        return "door"
    if extract_mark_dimensions(name, kind_hint="window"):
        return "window"
    # Common CAD mark conventions are intentionally accepted only on an
    # opening/block layer so generic blocks such as D1 or W1 elsewhere do not
    # become false openings.
    if opening_layer_context:
        if re.match(r"^(?:m|d|dd|sd|sl|sliding)[0-9]*$", normalized):
            return "door"
        if re.match(r"^(?:c|w|sw|cw|win|window)[0-9]*$", normalized):
            return "window"
    return None


def opening_category_from_block(kind: str, name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    if kind == "door":
        if (
            "双扇" in name
            or "双开" in name
            or normalized.startswith(("double", "dbl", "pair", "shuang", "dd"))
        ):
            return "double_swing_door"
        if (
            "推拉" in name
            or normalized.startswith(("tlm", "sliding", "slide", "sd", "sl", "pushpull", "pocket"))
        ):
            return "sliding_door"
    if kind == "window":
        if "推拉" in name or normalized.startswith(("tlc", "sliding", "slide", "sw")):
            return "sliding_window"
        if "平开" in name or normalized.startswith(("pkc", "casement", "cw")):
            return "casement_window"
    return "unknown"


def opening_width_from_block_bounds(ent: CadEntity) -> float | None:
    bounds = ent.data.get("block_bounds")
    if not isinstance(bounds, tuple) or len(bounds) != 4:
        return None
    width = max(abs(float(bounds[2]) - float(bounds[0])), abs(float(bounds[3]) - float(bounds[1])))
    return round(width, 3) if MIN_OPENING_WIDTH <= width <= MAX_OPENING_WIDTH else None


def is_near_existing_opening(
    point: tuple[float, float],
    openings: list[Opening],
    width: float | None,
    minimum: float = 450,
) -> bool:
    tolerance = minimum
    if width is not None:
        tolerance = max(minimum, min(1200.0, float(width) * 0.65))
    for opening in openings:
        existing_width = opening.width or 0
        existing_tolerance = max(tolerance, min(1200.0, existing_width * 0.65))
        if distance(point, tuple(opening.point)) <= existing_tolerance:
            return True
    return False


def arc_sweep(ent: CadEntity) -> float:
    start = float(ent.data.get("start_angle", 0) or 0) % 360
    end = float(ent.data.get("end_angle", 0) or 0) % 360
    sweep = (end - start) % 360
    return sweep if sweep <= 180 else 360 - sweep


def arc_open_direction(ent: CadEntity) -> str:
    start = float(ent.data.get("start_angle", 0) or 0)
    end = float(ent.data.get("end_angle", 0) or 0)
    mid = radians((start + ((end - start) % 360) / 2) % 360)
    dx = cos(mid)
    dy = sin(mid)
    if abs(dx) >= abs(dy):
        return "east" if dx >= 0 else "west"
    return "north" if dy >= 0 else "south"


def _nearest_wall(point: tuple[float, float], walls: list[Wall]) -> tuple[str | None, float | None]:
    if not walls:
        return None, None
    best_id = None
    best_dist = None
    best_length = -1.0
    for wall in walls:
        seg = Segment(wall.id, "wall", wall.start, wall.end, "wall")
        d = point_to_axis_distance(point, seg)
        if best_dist is None or d < best_dist - 50 or (abs(d - best_dist) <= 50 and wall.length > best_length):
            best_dist = d
            best_id = wall.id
            best_length = wall.length
    return best_id, best_dist


def round_point(point: tuple[float, float]) -> tuple[float, float]:
    return (round(float(point[0]), 3), round(float(point[1]), 3))
