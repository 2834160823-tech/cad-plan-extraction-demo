from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot


Point = tuple[float, float]


@dataclass
class Segment:
    id: str
    layer: str
    start: Point
    end: Point
    source: str

    @property
    def length(self) -> float:
        return distance(self.start, self.end)

    @property
    def orientation(self) -> str:
        dx = abs(self.end[0] - self.start[0])
        dy = abs(self.end[1] - self.start[1])
        if dx >= dy * 10:
            return "H"
        if dy >= dx * 10:
            return "V"
        return "OTHER"

    @property
    def midpoint(self) -> Point:
        return ((self.start[0] + self.end[0]) / 2, (self.start[1] + self.end[1]) / 2)

    @property
    def angle_degrees(self) -> float:
        return degrees(atan2(self.end[1] - self.start[1], self.end[0] - self.start[0]))


def distance(a: Point, b: Point) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


def normalize_segment(seg: Segment) -> Segment:
    if seg.orientation == "OTHER":
        if (seg.start[0], seg.start[1]) > (seg.end[0], seg.end[1]):
            return Segment(seg.id, seg.layer, seg.end, seg.start, seg.source)
        return seg
    if seg.orientation == "H" and seg.start[0] > seg.end[0]:
        return Segment(seg.id, seg.layer, seg.end, seg.start, seg.source)
    if seg.orientation == "V" and seg.start[1] > seg.end[1]:
        return Segment(seg.id, seg.layer, seg.end, seg.start, seg.source)
    return seg


def overlap_1d(a1: float, a2: float, b1: float, b2: float) -> tuple[float, float, float]:
    lo = max(min(a1, a2), min(b1, b2))
    hi = min(max(a1, a2), max(b1, b2))
    return lo, hi, max(0.0, hi - lo)


def unit_direction(seg: Segment) -> Point:
    length = seg.length
    if length == 0:
        return (1.0, 0.0)
    return ((seg.end[0] - seg.start[0]) / length, (seg.end[1] - seg.start[1]) / length)


def left_normal(direction: Point) -> Point:
    return (-direction[1], direction[0])


def dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def subtract(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def scale(a: Point, factor: float) -> Point:
    return (a[0] * factor, a[1] * factor)


def are_parallel(a: Segment, b: Segment, tolerance: float = 0.035) -> bool:
    da = unit_direction(a)
    db = unit_direction(b)
    return abs(cross(da, db)) <= tolerance


def projected_interval(seg: Segment, origin: Point, direction: Point) -> tuple[float, float]:
    s = dot(subtract(seg.start, origin), direction)
    e = dot(subtract(seg.end, origin), direction)
    return (min(s, e), max(s, e))


def parallel_offset(a: Segment, b: Segment) -> float:
    direction = unit_direction(a)
    normal = left_normal(direction)
    return abs(dot(subtract(b.start, a.start), normal))


def centerline_between_parallel_segments(a: Segment, b: Segment) -> tuple[Point, Point, float] | None:
    """Return centerline over the overlapped span for any pair of parallel segments."""

    if not are_parallel(a, b):
        return None
    direction = unit_direction(a)
    normal = left_normal(direction)
    offset = dot(subtract(b.start, a.start), normal)
    width = abs(offset)

    a0, a1 = projected_interval(a, a.start, direction)
    b0, b1 = projected_interval(b, a.start, direction)
    lo, hi, overlap = overlap_1d(a0, a1, b0, b1)
    if overlap <= 0:
        return None

    center_origin = add(a.start, scale(normal, offset / 2))
    start = add(center_origin, scale(direction, lo))
    end = add(center_origin, scale(direction, hi))
    return start, end, width


def point_to_axis_distance(point: Point, seg: Segment) -> float:
    x, y = point
    x1, y1 = seg.start
    x2, y2 = seg.end
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return distance(point, seg.start)
    t = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj = (x1 + t * dx, y1 + t * dy)
    return distance(point, proj)


def polygon_area(points: list[Point]) -> float:
    if len(points) < 3:
        return 0.0
    total = 0.0
    for a, b in zip(points, points[1:] + points[:1]):
        total += a[0] * b[1] - b[0] * a[1]
    return abs(total) / 2


def centroid(points: list[Point]) -> Point:
    if not points:
        return (0.0, 0.0)
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def bbox(points: list[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def nearest_standard(value: float, standards: list[float], tolerance: float) -> tuple[float, bool, float]:
    if not standards:
        return value, False, 0.0
    nearest = min(standards, key=lambda v: abs(v - value))
    delta = abs(nearest - value)
    return nearest, delta <= tolerance, delta
