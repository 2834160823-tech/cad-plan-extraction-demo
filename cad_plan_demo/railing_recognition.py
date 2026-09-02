from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from math import hypot

from .dxf_parser import CadEntity


RAILING_LAYER_RE = re.compile(
    r"railing|handrail|\u680f\u6746|\u6276\u624b|\u62a4\u680f|\u6b04\u687f",
    re.I,
)
HEIGHT_RE = re.compile(
    r"(?:\u680f\u6746|\u6276\u624b|\u62a4\u680f)?\s*"
    r"(?:\u9ad8|\u9ad8\u5ea6|height|h)\s*[:\uff1a]?\s*"
    r"(\d+(?:\.\d+)?)\s*(mm|m|\u7c73)?",
    re.I,
)


@dataclass
class SegmentInfo:
    start: tuple[float, float]
    end: tuple[float, float]
    layer: str

    @property
    def length(self) -> float:
        return hypot(self.end[0] - self.start[0], self.end[1] - self.start[1])


@dataclass
class RailingCandidate:
    id: str
    start: tuple[float, float]
    end: tuple[float, float]
    height_mm: float | None
    distance_to_stairwell_mm: float | None
    related_stair_id: str | None
    source: str
    source_geometry_count: int
    confidence: float
    needs_review: bool
    remarks: str


@dataclass
class RailingHeightCandidate:
    height_mm: float
    source: str
    confidence: float
    source_geometry_count: int


def recognize_railings(entities: list[CadEntity], result: dict) -> None:
    segments = railing_segments(entities)
    height_candidates = railing_height_candidates(entities, result)
    railings = build_railing_candidates(segments, entities, result)
    result["railings"] = [asdict(item) for item in railings]
    result["railing_height_candidates"] = [asdict(item) for item in height_candidates]
    result.setdefault("counts", {})["railings"] = len(railings)
    result.setdefault("plan_summary", {}).setdefault("counts", {})["railings"] = len(railings)


def enrich_railings_with_section_height(workbook_results: list[tuple[str, dict]]) -> int:
    section_height = best_project_section_height(workbook_results)
    if section_height is None:
        return 0
    filled = 0
    for _name, result in workbook_results:
        if drawing_type_is_section_or_detail(result):
            continue
        for railing in result.get("railings", []):
            if railing.get("height_mm") not in (None, ""):
                continue
            railing["height_mm"] = round(section_height.height_mm, 3)
            railing["confidence"] = round(max(float(railing.get("confidence") or 0), 0.74), 3)
            railing["needs_review"] = bool(railing.get("distance_to_stairwell_mm") in (None, ""))
            remarks = str(railing.get("remarks") or "").strip()
            addition = "height filled from stair section/detail vertical railing line."
            railing["remarks"] = f"{remarks} {addition}".strip()
            filled += 1
    return filled


def best_project_section_height(workbook_results: list[tuple[str, dict]]) -> RailingHeightCandidate | None:
    candidates: list[RailingHeightCandidate] = []
    for _name, result in workbook_results:
        if not drawing_type_is_section_or_detail(result):
            continue
        for item in result.get("railing_height_candidates", []):
            try:
                candidates.append(
                    RailingHeightCandidate(
                        height_mm=float(item.get("height_mm")),
                        source=str(item.get("source") or ""),
                        confidence=float(item.get("confidence") or 0),
                        source_geometry_count=int(item.get("source_geometry_count") or 1),
                    )
                )
            except (TypeError, ValueError):
                continue
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item.confidence, item.height_mm), reverse=True)[0]


def railing_segments(entities: list[CadEntity]) -> list[SegmentInfo]:
    segments: list[SegmentInfo] = []
    for ent in entities:
        if not RAILING_LAYER_RE.search(ent.layer or ""):
            continue
        segments.extend(entity_segments(ent))
    return [seg for seg in segments if seg.length >= 200]


def entity_segments(ent: CadEntity) -> list[SegmentInfo]:
    data = ent.data or {}
    if ent.type == "LINE":
        start = point_tuple(data.get("start"))
        end = point_tuple(data.get("end"))
        return [SegmentInfo(start, end, ent.layer)] if start and end else []
    if ent.type in {"LWPOLYLINE", "POLYLINE"}:
        points = [point_tuple(item) for item in data.get("points", [])]
        clean = [item for item in points if item is not None]
        segments = [SegmentInfo(a, b, ent.layer) for a, b in zip(clean, clean[1:])]
        if data.get("closed") and len(clean) > 2:
            segments.append(SegmentInfo(clean[-1], clean[0], ent.layer))
        return segments
    return []


def build_railing_candidates(
    segments: list[SegmentInfo],
    entities: list[CadEntity],
    result: dict,
) -> list[RailingCandidate]:
    used: set[int] = set()
    candidates: list[RailingCandidate] = []
    height_info = best_railing_height_candidate(entities, result)
    height = height_info.height_mm if height_info else None
    origin = result.get("coordinate_system", {}).get("origin", [0.0, 0.0])
    for index, seg in enumerate(segments):
        if index in used:
            continue
        pair_index = best_pair_index(index, seg, segments, used)
        if pair_index is not None:
            other = segments[pair_index]
            used.update({index, pair_index})
            start, end = centerline(seg, other)
            source = "paired_railing_lines"
            source_count = 2
            confidence = 0.84
        else:
            used.add(index)
            start, end = seg.start, seg.end
            source = "single_railing_line"
            source_count = 1
            confidence = 0.62
        local_start = local_point(start, origin)
        local_end = local_point(end, origin)
        distance, stair_id = distance_to_stairwell((local_start, local_end), result)
        missing = []
        if height is None:
            missing.append("height")
        if distance is None:
            missing.append("distance_to_stairwell")
        needs_review = bool(missing)
        if needs_review:
            confidence = max(0.35, confidence - 0.08 * len(missing))
        candidates.append(
            RailingCandidate(
                id=f"RAIL{len(candidates) + 1:04d}",
                start=round_pair(local_start),
                end=round_pair(local_end),
                height_mm=round(height, 3) if height is not None else None,
                distance_to_stairwell_mm=round(distance, 3) if distance is not None else None,
                related_stair_id=stair_id,
                source=source,
                source_geometry_count=source_count,
                confidence=round(confidence, 3),
                needs_review=needs_review,
                remarks=railing_remarks(missing, height_info),
            )
        )
    return merge_duplicate_railings(candidates)


def railing_height_candidates(entities: list[CadEntity], result: dict) -> list[RailingHeightCandidate]:
    candidates: list[RailingHeightCandidate] = []
    text_height = parse_railing_height(entities)
    if text_height is not None:
        candidates.append(RailingHeightCandidate(round(text_height, 3), "height_text", 0.9, 1))
    vertical_height = infer_section_railing_height_from_vertical_line(entities, result)
    if vertical_height is not None:
        candidates.append(
            RailingHeightCandidate(
                round(vertical_height, 3),
                "section_vertical_railing_line",
                0.82,
                1,
            )
        )
    return candidates


def best_railing_height_candidate(entities: list[CadEntity], result: dict) -> RailingHeightCandidate | None:
    candidates = railing_height_candidates(entities, result)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.confidence, reverse=True)[0]


def infer_section_railing_height_from_vertical_line(entities: list[CadEntity], result: dict) -> float | None:
    if not drawing_type_is_section_or_detail(result):
        return None
    vertical_segments = [
        seg for seg in railing_segments(entities) if is_vertical_segment(seg) and 800 <= seg.length <= 1400
    ]
    if not vertical_segments:
        return None
    return max(vertical_segments, key=lambda seg: seg.length).length


def is_vertical_segment(seg: SegmentInfo) -> bool:
    dx = abs(seg.end[0] - seg.start[0])
    dy = abs(seg.end[1] - seg.start[1])
    return dy >= 1 and dx <= max(5.0, dy * 0.05)


def drawing_type_is_section_or_detail(result: dict) -> bool:
    notes = result.get("notes", {})
    drawing_type = notes.get("drawing_type")
    title = str(notes.get("drawing_title") or "").lower()
    return drawing_type in {"architectural_section", "architectural_detail"} or any(
        keyword in title
        for keyword in ("section", "detail", "\u5256\u9762", "\u8be6\u56fe")
    )


def railing_remarks(missing: list[str], height_info: RailingHeightCandidate | None) -> str:
    parts: list[str] = []
    if missing:
        parts.append("missing: " + ", ".join(missing))
    if height_info and height_info.source == "section_vertical_railing_line":
        parts.append("height inferred from the longest vertical railing line in stair section/detail.")
    return " ".join(parts)


def best_pair_index(index: int, seg: SegmentInfo, segments: list[SegmentInfo], used: set[int]) -> int | None:
    best: tuple[float, int] | None = None
    for other_index, other in enumerate(segments):
        if other_index == index or other_index in used:
            continue
        if not are_parallel(seg, other):
            continue
        if abs(seg.length - other.length) > max(seg.length, other.length) * 0.25:
            continue
        distance = segment_axis_distance(seg, other)
        if not (20 <= distance <= 350):
            continue
        overlap = projected_overlap_ratio(seg, other)
        if overlap < 0.65:
            continue
        score = distance + (1.0 - overlap) * 100
        if best is None or score < best[0]:
            best = (score, other_index)
    return best[1] if best else None


def are_parallel(a: SegmentInfo, b: SegmentInfo) -> bool:
    au = unit(a)
    bu = unit(b)
    return abs(au[0] * bu[0] + au[1] * bu[1]) >= 0.98


def unit(seg: SegmentInfo) -> tuple[float, float]:
    length = seg.length or 1.0
    return ((seg.end[0] - seg.start[0]) / length, (seg.end[1] - seg.start[1]) / length)


def segment_axis_distance(a: SegmentInfo, b: SegmentInfo) -> float:
    au = unit(a)
    normal = (-au[1], au[0])
    return abs((b.start[0] - a.start[0]) * normal[0] + (b.start[1] - a.start[1]) * normal[1])


def projected_overlap_ratio(a: SegmentInfo, b: SegmentInfo) -> float:
    au = unit(a)
    a_vals = sorted([dot(a.start, au), dot(a.end, au)])
    b_vals = sorted([dot(b.start, au), dot(b.end, au)])
    overlap = max(0.0, min(a_vals[1], b_vals[1]) - max(a_vals[0], b_vals[0]))
    return overlap / max(1.0, min(a.length, b.length))


def centerline(a: SegmentInfo, b: SegmentInfo) -> tuple[tuple[float, float], tuple[float, float]]:
    au = unit(a)
    points = [a.start, a.end, b.start, b.end]
    ordered = sorted(points, key=lambda point: dot(point, au))
    start = midpoint(ordered[0], ordered[1])
    end = midpoint(ordered[2], ordered[3])
    return start, end


def parse_railing_height(entities: list[CadEntity]) -> float | None:
    for ent in entities:
        if ent.type not in {"TEXT", "MTEXT"}:
            continue
        text = str((ent.data or {}).get("text") or "")
        match = HEIGHT_RE.search(text)
        if not match:
            continue
        value = float(match.group(1))
        unit = (match.group(2) or "mm").lower()
        if unit in {"m", "\u7c73"} or value < 20:
            value *= 1000
        if 600 <= value <= 1600:
            return value
    return None


def distance_to_stairwell(
    line: tuple[tuple[float, float], tuple[float, float]],
    result: dict,
) -> tuple[float | None, str | None]:
    best: tuple[float, str | None] | None = None
    midpoint_line = midpoint(line[0], line[1])
    for stair in result.get("stairs", []):
        boundary = stair.get("stairwell_opening_boundary") or stair.get("boundary_points")
        points = [point_tuple(item) for item in boundary or []]
        clean = [item for item in points if item is not None]
        if len(clean) < 2:
            continue
        closed = clean + [clean[0]]
        distance = min(point_to_segment_distance(midpoint_line, a, b) for a, b in zip(closed, closed[1:]))
        if best is None or distance < best[0]:
            best = (distance, stair.get("id"))
    for opening in result.get("floor_openings", []):
        boundary = opening.get("local_boundary_points") or opening.get("boundary_points")
        points = [point_tuple(item) for item in boundary or []]
        clean = [item for item in points if item is not None]
        if len(clean) < 2:
            continue
        closed = clean + [clean[0]]
        distance = min(point_to_segment_distance(midpoint_line, a, b) for a, b in zip(closed, closed[1:]))
        if best is None or distance < best[0]:
            best = (distance, opening.get("id"))
    return best if best is not None else (None, None)


def merge_duplicate_railings(candidates: list[RailingCandidate]) -> list[RailingCandidate]:
    unique: list[RailingCandidate] = []
    for item in candidates:
        if any(railings_are_duplicates(item, other) for other in unique):
            continue
        item.id = f"RAIL{len(unique) + 1:04d}"
        unique.append(item)
    return unique


def railings_are_duplicates(a: RailingCandidate, b: RailingCandidate) -> bool:
    a_segment = SegmentInfo(a.start, a.end, "")
    b_segment = SegmentInfo(b.start, b.end, "")
    if not are_parallel(a_segment, b_segment):
        return False
    if segment_axis_distance(a_segment, b_segment) >= 50:
        return False
    return projected_overlap_ratio(a_segment, b_segment) >= 0.85


def line_distance(a1: tuple[float, float], a2: tuple[float, float], b1: tuple[float, float], b2: tuple[float, float]) -> float:
    return min(
        point_to_segment_distance(a1, b1, b2),
        point_to_segment_distance(a2, b1, b2),
        point_to_segment_distance(b1, a1, a2),
        point_to_segment_distance(b2, a1, a2),
    )


def point_to_segment_distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    ab = (b[0] - a[0], b[1] - a[1])
    ap = (p[0] - a[0], p[1] - a[1])
    length_sq = ab[0] ** 2 + ab[1] ** 2
    if length_sq <= 0:
        return hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, (ap[0] * ab[0] + ap[1] * ab[1]) / length_sq))
    projection = (a[0] + ab[0] * t, a[1] + ab[1] * t)
    return hypot(p[0] - projection[0], p[1] - projection[1])


def point_tuple(value: object) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def midpoint(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def dot(point: tuple[float, float], direction: tuple[float, float]) -> float:
    return point[0] * direction[0] + point[1] * direction[1]


def local_point(point: tuple[float, float], origin: object) -> tuple[float, float]:
    if isinstance(origin, (list, tuple)) and len(origin) >= 2:
        return point[0] - float(origin[0]), point[1] - float(origin[1])
    return point


def round_pair(value: tuple[float, float]) -> tuple[float, float]:
    return round(value[0], 3), round(value[1], 3)
