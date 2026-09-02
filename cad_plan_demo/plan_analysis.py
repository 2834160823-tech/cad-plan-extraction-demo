from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from .dxf_parser import CadEntity
from .notes import TextItem, extract_text_items


@dataclass
class FloorHeightCandidate:
    floor: str | None
    height_mm: float
    raw_text: str
    layer: str
    point: tuple[float, float]
    confidence: float


@dataclass
class ElevationMark:
    label: str | None
    elevation_mm: float
    raw_text: str
    layer: str
    point: tuple[float, float]
    confidence: float


def analyze_plan(entities: list[CadEntity], result: dict) -> dict:
    text_items = extract_text_items(entities)
    drawing_type = result.get("notes", {}).get("drawing_type")
    elevation_marks = detect_elevation_marks(text_items, drawing_type)
    explicit_heights = detect_floor_heights(text_items)
    derived_heights = derive_floor_heights_from_elevation_marks(elevation_marks)
    floor_heights = merge_height_candidates(explicit_heights + derived_heights)
    openings = result.get("openings", [])
    doors = [o for o in openings if o.get("kind") == "door"]
    windows = [o for o in openings if o.get("kind") == "window"]

    return {
        "drawing_type": result.get("notes", {}).get("drawing_type"),
        "drawing_title": result.get("notes", {}).get("drawing_title"),
        "floor_heights": [asdict(item) for item in floor_heights],
        "elevation_marks": [asdict(item) for item in elevation_marks],
        "counts": {
            "doors": len(doors),
            "windows": len(windows),
            "openings_total": len(openings),
            "columns": len(result.get("columns", [])),
            "floors": len(result.get("floors", [])),
            "floor_openings": len(result.get("floor_openings", [])),
            "walls": len(result.get("walls", [])),
            "axes": len(result.get("axes", [])),
            "elevation_marks": len(elevation_marks),
        },
        "revit_recommended_next_fields": [
            "level_name_and_elevation",
            "wall_type_and_height",
            "door_window_type_and_size",
            "room_boundary_and_room_name",
            "grid_axis_names",
            "floor_slab_boundary_and_thickness",
            "material_and_fire_rating_from_general_notes",
        ],
    }


def detect_floor_heights(text_items: list[TextItem]) -> list[FloorHeightCandidate]:
    candidates: list[FloorHeightCandidate] = []
    for item in text_items:
        text = item.text.strip()
        if not text:
            continue
        for match in find_height_matches(text):
            floor = match.get("floor")
            height_mm = normalize_height_to_mm(match["value"], match.get("unit"))
            if height_mm is None:
                continue
            confidence = 0.82 if floor else 0.62
            if re.search(r"\u5c42\u9ad8|floor\s*height|storey\s*height", text, re.I):
                confidence += 0.12
            candidates.append(
                FloorHeightCandidate(
                    floor=floor,
                    height_mm=round(height_mm, 3),
                    raw_text=text,
                    layer=item.layer,
                    point=item.point,
                    confidence=round(min(confidence, 0.95), 3),
                )
            )
    return merge_height_candidates(candidates)


def find_height_matches(text: str) -> list[dict]:
    matches: list[dict] = []
    patterns = [
        re.compile(
            r"(?P<floor>[\u4e00-\u9fa5A-Za-z0-9#\-~]+?\u5c42)?[^\n\u3002\uff1b;]{0,8}?"
            r"\u5c42\u9ad8\s*[:\uff1a=]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|m|\u7c73)?",
            re.I,
        ),
        re.compile(
            r"(?P<floor>[\u4e00-\u9fa5A-Za-z0-9#\-~]+?\u5c42)[^\n\u3002\uff1b;]{0,8}?"
            r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|m|\u7c73)",
            re.I,
        ),
        re.compile(
            r"(floor|storey)\s*height\s*[:=]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|m)?",
            re.I,
        ),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            matches.append(
                {
                    "floor": clean_floor_name(match.groupdict().get("floor")),
                    "value": match.group("value"),
                    "unit": match.groupdict().get("unit"),
                }
            )
    return matches


def detect_elevation_marks(text_items: list[TextItem], drawing_type: str | None = None) -> list[ElevationMark]:
    marks: list[ElevationMark] = []
    for item in text_items:
        text = item.text.strip()
        if not text:
            continue
        for match in find_elevation_matches(text, drawing_type):
            elevation_mm = normalize_elevation_to_mm(match["value"])
            if elevation_mm is None:
                continue
            label = match.get("label") or infer_level_label(text, match["value"])
            confidence = 0.68
            if has_elevation_context(text):
                confidence += 0.14
            if label:
                confidence += 0.08
            marks.append(
                ElevationMark(
                    label=clean_floor_name(label),
                    elevation_mm=round(elevation_mm, 3),
                    raw_text=text,
                    layer=item.layer,
                    point=item.point,
                    confidence=round(min(confidence, 0.95), 3),
                )
            )
    return merge_elevation_marks(marks)


def find_elevation_matches(text: str, drawing_type: str | None = None) -> list[dict]:
    matches: list[dict] = []
    normalized = normalize_signs(text)
    patterns = [
        re.compile(
            r"(?P<label>[\u4e00-\u9fa5A-Za-z0-9#\-~]{0,16}?"
            r"(?:\u5c42|\u5c4b\u9762|\u5730\u576a|\u5973\u513f\u5899|roof|level|floor|el)?)"
            r"\s*(?:\u6807\u9ad8|EL\.?|LEVEL)?\s*[:\uff1a=]?\s*"
            r"(?P<value>[+\-\u00b1]?\s*\d{1,5}(?:\.\d{1,4})?)",
            re.I,
        ),
    ]
    for pattern in patterns:
        for match in pattern.finditer(normalized):
            value = match.group("value")
            if not looks_like_elevation_text(normalized, value, drawing_type):
                continue
            matches.append({"label": match.groupdict().get("label"), "value": value})
    return matches


def looks_like_elevation_text(text: str, value: str, drawing_type: str | None = None) -> bool:
    compact = value.replace(" ", "")
    has_sign = compact.startswith(("+", "-", "\u00b1"))
    if has_sign:
        return True
    if has_elevation_context(text) and re.search(r"\d+\.\d{2,4}", compact):
        return True
    if drawing_type in {"architectural_elevation", "architectural_section"} and is_standalone_elevation_number(text, compact):
        return True
    return False


def is_standalone_elevation_number(text: str, compact_value: str) -> bool:
    cleaned = normalize_signs(text).strip()
    cleaned = re.sub(r"^\|[^;\n]{1,120};", "", cleaned).strip()
    if cleaned != compact_value:
        return False
    if not re.fullmatch(r"\d{1,2}\.\d{3,4}", compact_value):
        return False
    return abs(float(compact_value)) <= 30


def has_elevation_context(text: str) -> bool:
    return bool(
        re.search(
            r"\u6807\u9ad8|\u5c42|\u5c4b\u9762|\u5730\u576a|\u5973\u513f\u5899|EL\.?|LEVEL|ELEV",
            text,
            re.I,
        )
    )


def infer_level_label(text: str, value: str) -> str | None:
    before = normalize_signs(text).split(value.strip(), 1)[0]
    label_patterns = [
        r"(\u5730\u4e0b?[\u4e00-\u9fa5\d]+?\u5c42)",
        r"([\u4e00-\u9fa5\d#]+?\u5c42)",
        r"(\u5c4b\u9762)",
        r"(\u5973\u513f\u5899)",
        r"(\u5ba4\u5916\u5730\u576a|\u5ba4\u5185\u5730\u576a|\u5730\u576a)",
        r"((?:roof|level|floor)\s*[A-Za-z0-9#\-]+)",
    ]
    for pattern in label_patterns:
        found = re.findall(pattern, before, flags=re.I)
        if found:
            return found[-1]
    return None


def derive_floor_heights_from_elevation_marks(marks: list[ElevationMark]) -> list[FloorHeightCandidate]:
    unique: list[ElevationMark] = []
    seen: set[float] = set()
    for mark in sorted(marks, key=lambda m: (m.elevation_mm, m.point[0])):
        key = round(mark.elevation_mm, 1)
        if key in seen:
            continue
        seen.add(key)
        unique.append(mark)

    candidates: list[FloorHeightCandidate] = []
    for lower, upper in zip(unique, unique[1:]):
        height = upper.elevation_mm - lower.elevation_mm
        if height < 1500 or height > 8000:
            continue
        floor = join_level_names(lower.label, upper.label)
        raw = f"Derived from elevation marks: {lower.raw_text} -> {upper.raw_text}"
        candidates.append(
            FloorHeightCandidate(
                floor=floor,
                height_mm=round(height, 3),
                raw_text=raw,
                layer=upper.layer,
                point=upper.point,
                confidence=0.72,
            )
        )
    return candidates


def join_level_names(lower: str | None, upper: str | None) -> str | None:
    if lower and upper:
        return f"{lower}->{upper}"
    return upper or lower


def clean_floor_name(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"[\s:=,\uff1a\uff0c]+", "", value)
    return value or None


def normalize_height_to_mm(value: str, unit: str | None) -> float | None:
    numeric = float(value)
    unit = (unit or "").lower()
    if unit in {"m", "\u7c73"}:
        return numeric * 1000
    if unit == "mm":
        return numeric
    if numeric < 20:
        return numeric * 1000
    return numeric


def normalize_elevation_to_mm(value: str) -> float | None:
    compact = normalize_signs(value).replace(" ", "")
    if not compact:
        return None
    if compact.startswith("\u00b1"):
        compact = compact[1:]
    try:
        numeric = float(compact)
    except ValueError:
        return None
    if abs(numeric) < 100:
        return numeric * 1000
    return numeric


def normalize_signs(text: str) -> str:
    return (
        text.replace("\uff0b", "+")
        .replace("\ufe62", "+")
        .replace("\uff0d", "-")
        .replace("\u2212", "-")
        .replace("+/-", "\u00b1")
    )


def merge_height_candidates(candidates: list[FloorHeightCandidate]) -> list[FloorHeightCandidate]:
    merged: list[FloorHeightCandidate] = []
    seen: set[tuple[str | None, float]] = set()
    for item in sorted(candidates, key=lambda c: (-c.confidence, c.point[1], c.point[0])):
        key = (item.floor, round(item.height_mm, 1))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def merge_elevation_marks(marks: list[ElevationMark]) -> list[ElevationMark]:
    merged: list[ElevationMark] = []
    seen: set[tuple[str | None, float]] = set()
    for item in sorted(marks, key=lambda m: (-m.confidence, m.elevation_mm, m.point[0])):
        key = (item.label, round(item.elevation_mm, 1))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged
