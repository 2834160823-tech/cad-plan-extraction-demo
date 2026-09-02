from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from .dxf_parser import CadEntity
from .geometry import bbox, centroid, polygon_area


FRAME_LAYER_RE = re.compile(
    r"(\u56fe\u7eb8\u5c42|\u56fe\u6846\u5c42|\u56fe\u6846|\u56fe\u7eb8\u6846|"
    r"drawing[\s_-]*border|sheet[\s_-]*border|frame|sheet|layout|viewport)",
    re.I,
)


@dataclass
class DrawingFrame:
    id: str
    layer: str
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    width: float
    height: float
    center: tuple[float, float]
    source: str
    confidence: float

    def to_dict(self) -> dict:
        return asdict(self)


def is_frame_layer(layer: str) -> bool:
    return bool(FRAME_LAYER_RE.search(layer or ""))


def detect_drawing_frames(entities: list[CadEntity]) -> list[DrawingFrame]:
    frames: list[DrawingFrame] = []
    for ent in entities:
        if ent.type not in {"LWPOLYLINE", "POLYLINE"}:
            continue
        if not is_frame_layer(ent.layer):
            continue
        points = [tuple(p) for p in ent.data.get("points", [])]
        if not ent.data.get("closed") or len(points) < 4:
            continue
        frame = frame_from_points(len(frames) + 1, ent.layer, points, ent.type)
        if frame is not None:
            frames.append(frame)

    frames = remove_duplicate_frames(frames)
    frames.sort(key=lambda f: (-f.max_y, f.min_x))
    for index, frame in enumerate(frames, start=1):
        frame.id = f"F{index:03d}"
    return frames


def frame_from_points(index: int, layer: str, points: list[tuple[float, float]], source: str) -> DrawingFrame | None:
    min_x, min_y, max_x, max_y = bbox(points)
    width = max_x - min_x
    height = max_y - min_y
    if width < 1000 or height < 1000:
        return None
    area = polygon_area(points)
    box_area = width * height
    if box_area <= 0:
        return None
    fill_ratio = area / box_area
    if fill_ratio < 0.82:
        return None
    center = centroid(points)
    return DrawingFrame(
        id=f"F{index:03d}",
        layer=layer,
        min_x=round(min_x, 3),
        min_y=round(min_y, 3),
        max_x=round(max_x, 3),
        max_y=round(max_y, 3),
        width=round(width, 3),
        height=round(height, 3),
        center=(round(center[0], 3), round(center[1], 3)),
        source=source,
        confidence=0.88,
    )


def remove_duplicate_frames(frames: list[DrawingFrame]) -> list[DrawingFrame]:
    unique: list[DrawingFrame] = []
    for frame in sorted(frames, key=lambda item: item.width * item.height, reverse=True):
        if any(frames_overlap(frame, existing) > 0.92 for existing in unique):
            continue
        unique.append(frame)
    return unique


def frames_overlap(a: DrawingFrame, b: DrawingFrame) -> float:
    x_overlap = max(0.0, min(a.max_x, b.max_x) - max(a.min_x, b.min_x))
    y_overlap = max(0.0, min(a.max_y, b.max_y) - max(a.min_y, b.min_y))
    overlap_area = x_overlap * y_overlap
    if overlap_area <= 0:
        return 0.0
    smaller = min(a.width * a.height, b.width * b.height)
    return overlap_area / smaller if smaller else 0.0


def filter_entities_to_frame(entities: list[CadEntity], frame: DrawingFrame, margin: float = 0.0) -> list[CadEntity]:
    filtered: list[CadEntity] = []
    for ent in entities:
        if is_frame_layer(ent.layer):
            continue
        point = representative_point(ent)
        if point is None:
            continue
        if point_in_frame(point, frame, margin):
            filtered.append(ent)
    return filtered


def point_in_frame(point: tuple[float, float], frame: DrawingFrame, margin: float = 0.0) -> bool:
    x, y = point
    return (
        frame.min_x - margin <= x <= frame.max_x + margin
        and frame.min_y - margin <= y <= frame.max_y + margin
    )


def representative_point(ent: CadEntity) -> tuple[float, float] | None:
    if ent.type == "LINE":
        start = tuple(ent.data.get("start", (0.0, 0.0)))
        end = tuple(ent.data.get("end", (0.0, 0.0)))
        return ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    if ent.type == "CIRCLE":
        return tuple(ent.data.get("center", (0.0, 0.0)))
    if ent.type == "ARC":
        return tuple(ent.data.get("center", (0.0, 0.0)))
    if ent.type in {"TEXT", "MTEXT", "INSERT", "DIMENSION"}:
        return tuple(ent.data.get("point", (0.0, 0.0)))
    if ent.type in {"LWPOLYLINE", "POLYLINE"}:
        points = [tuple(p) for p in ent.data.get("points", [])]
        if not points:
            return None
        return centroid(points)
    return None


def frame_summary_rows(frames: list[DrawingFrame]) -> list[dict]:
    return [frame.to_dict() for frame in frames]
