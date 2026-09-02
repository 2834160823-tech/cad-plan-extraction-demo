from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from math import hypot

from .dxf_parser import CadEntity
from .geometry import bbox


STAIR_TEXT_RE = re.compile(r"楼梯|梯段|stair", re.I)
STEP_COUNT_RE = re.compile(r"(\d+)\s*(?:级|步|踏|steps?|risers?)", re.I)
TREAD_RE = re.compile(r"(?:踏步|踏面|踢面|tread|riser|step)\s*[:：=]?\s*(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)", re.I)
TREAD_WIDTH_RE = re.compile(r"(?:踏步宽|踏面宽|踏面|tread)\s*[:：=]?\s*(\d+(?:\.\d+)?)", re.I)
RISER_HEIGHT_RE = re.compile(r"(?:踏步高|踢面高|踢面|riser)\s*[:：=]?\s*(\d+(?:\.\d+)?)", re.I)
HEIGHT_RE = re.compile(r"(?:层高|总高|floor\s*height|storey\s*height|rise)\s*[:：=]?\s*(\d+(?:\.\d+)?)\s*(mm|m|米)?", re.I)
LEVEL_RANGE_RE = re.compile(r"([一二三四五六七八九十\d]+层?)\s*[-~至到]\s*([一二三四五六七八九十\d]+层?)")
STAIR_LAYER_RE = re.compile(r"stair|楼梯|梯", re.I)
EXCLUDED_FALLBACK_LAYER_RE = re.compile(r"axis|轴|wall|墙|window|窗|door|门|column|柱|图框|frame|title", re.I)


@dataclass
class StairCandidate:
    id: str
    stair_type: str
    start_level: str | None
    end_level: str | None
    start: tuple[float, float] | None
    end: tuple[float, float] | None
    boundary_points: list[tuple[float, float]]
    stairwell_opening_boundary: list[tuple[float, float]]
    opening_required: bool
    total_rise_mm: float | None
    total_run_mm: float | None
    width_mm: float | None
    stairwell_width_mm: float | None
    run_count: int
    risers_per_run: int | None
    treads_per_run: int | None
    run_length_mm: float | None
    landing_length_mm: float | None
    landing_width_mm: float | None
    riser_height_mm: float | None
    tread_depth_mm: float | None
    number_of_risers: int | None
    number_of_treads: int | None
    direction: str
    source: str
    source_segment_count: int
    confidence: float
    needs_review: bool
    remarks: str


@dataclass
class SegmentInfo:
    start: tuple[float, float]
    end: tuple[float, float]
    layer: str

    @property
    def length(self) -> float:
        return hypot(self.end[0] - self.start[0], self.end[1] - self.start[1])

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (
            min(self.start[0], self.end[0]),
            min(self.start[1], self.end[1]),
            max(self.start[0], self.end[0]),
            max(self.start[1], self.end[1]),
        )

    @property
    def orientation(self) -> str:
        dx = abs(self.end[0] - self.start[0])
        dy = abs(self.end[1] - self.start[1])
        if dx <= 1.0 and dy > 1.0:
            return "V"
        if dy <= 1.0 and dx > 1.0:
            return "H"
        return "O"


@dataclass
class GeometryDetails:
    stair_box: tuple[float, float, float, float] | None
    source_segment_count: int
    tread_depth_mm: float | None
    riser_height_mm: float | None
    number_of_risers: int | None
    number_of_treads: int | None
    width_mm: float | None
    stairwell_width_mm: float | None
    landing_length_mm: float | None
    landing_width_mm: float | None


def recognize_stairs(entities: list[CadEntity], result: dict) -> None:
    notes = result.get("notes", {})
    text_items = notes.get("text_items", [])
    text = "\n".join(str(item.get("text") or "") for item in text_items)
    if not STAIR_TEXT_RE.search(text):
        result["stairs"] = []
        result.setdefault("counts", {})["stairs"] = 0
        return

    drawing_type = notes.get("drawing_type")
    if drawing_type not in {"architectural_detail", "architectural_section", "architectural_plan", "mixed_or_unknown", None}:
        result["stairs"] = []
        result.setdefault("counts", {})["stairs"] = 0
        return

    candidate = build_double_run_stair_candidate(text, entities, result)
    result["stairs"] = [asdict(candidate)] if candidate else []
    result.setdefault("counts", {})["stairs"] = len(result["stairs"])
    result.setdefault("plan_summary", {}).setdefault("counts", {})["stairs"] = len(result["stairs"])


def enrich_stairs_with_project_floor_height(drawing_results: list[tuple[str, dict]]) -> None:
    floor_height = common_project_floor_height(drawing_results)
    project_riser_height = common_project_stair_riser_height(drawing_results)
    if project_riser_height:
        apply_project_stair_riser_height(drawing_results, project_riser_height)
    if not floor_height:
        return
    for _drawing_name, result in drawing_results:
        for stair in result.get("stairs", []):
            riser_height = stair.get("riser_height_mm")
            if not riser_height:
                continue
            inferred_risers = round(float(floor_height) / float(riser_height))
            if inferred_risers <= 0:
                continue
            current_risers = int(stair.get("number_of_risers") or 0)
            run_count = int(stair.get("run_count") or 2)
            tread_based_risers = int(stair.get("number_of_treads") or 0) + run_count
            if current_risers and inferred_risers < current_risers and inferred_risers != tread_based_risers:
                continue
            stair["total_rise_mm"] = round(float(floor_height), 3)
            stair["number_of_risers"] = inferred_risers
            stair["number_of_treads"] = max(inferred_risers - run_count, 0)
            stair["risers_per_run"] = round(inferred_risers / run_count) if run_count else None
            stair["treads_per_run"] = round(stair["number_of_treads"] / run_count) if run_count else None
            tread_depth = stair.get("tread_depth_mm")
            landing_width = stair.get("landing_width_mm") or 0
            if tread_depth:
                stair["run_length_mm"] = round(float(tread_depth) * stair["treads_per_run"], 3)
                stair["total_run_mm"] = round(stair["run_length_mm"] * 2 + float(landing_width), 3)
            stair["stair_core_id"] = stair.get("stair_core_id") or stair.get("element_id") or stair.get("id")
            stair["observed_interval_policy"] = "single_adjacent_interval"
            stair["remarks"] = (
                str(stair.get("remarks") or "")
                + f" 已按单个相邻楼层段使用层高{floor_height:g}/踏步高{float(riser_height):g}反推总级数{inferred_risers}；多层延续由空间智能体依据明确洞口逐段判断。"
            ).strip()


def common_project_floor_height(drawing_results: list[tuple[str, dict]]) -> float | None:
    heights: list[float] = []
    for _drawing_name, result in drawing_results:
        for item in result.get("plan_summary", {}).get("floor_heights", []):
            try:
                height = float(item.get("height_mm") or 0)
            except (TypeError, ValueError):
                continue
            if 1800 <= height <= 6000:
                heights.append(height)
    if not heights:
        return None
    counts = Counter(round(height / 10.0) * 10.0 for height in heights)
    return float(counts.most_common(1)[0][0])


def common_project_stair_riser_height(drawing_results: list[tuple[str, dict]]) -> float | None:
    values: list[float] = []
    for _drawing_name, result in drawing_results:
        for stair in result.get("stairs", []):
            try:
                value = float(stair.get("riser_height_mm") or 0)
            except (TypeError, ValueError):
                continue
            if 120 <= value <= 180:
                values.append(value)
    if not values:
        return None
    counts = Counter(round(value / 5.0) * 5.0 for value in values)
    return float(counts.most_common(1)[0][0])


def apply_project_stair_riser_height(drawing_results: list[tuple[str, dict]], project_riser_height: float) -> None:
    for _drawing_name, result in drawing_results:
        for stair in result.get("stairs", []):
            try:
                current = float(stair.get("riser_height_mm") or 0)
            except (TypeError, ValueError):
                current = 0.0
            if current and 120 <= current <= 180:
                continue
            if current and current < 120:
                continue
            stair["riser_height_mm"] = round(project_riser_height, 3)
            run_count = int(stair.get("run_count") or 2)
            treads = int(stair.get("number_of_treads") or 0)
            risers = int(stair.get("number_of_risers") or 0)
            if treads and (not risers or abs(risers - (treads + run_count)) <= run_count):
                risers = treads + run_count
                stair["number_of_risers"] = risers
            if risers:
                stair["total_rise_mm"] = round(project_riser_height * risers, 3)
                stair["risers_per_run"] = round(risers / run_count) if run_count else None
            if treads:
                stair["treads_per_run"] = round(treads / run_count) if run_count else None
            tread_depth = stair.get("tread_depth_mm")
            treads_per_run = stair.get("treads_per_run")
            if tread_depth and treads_per_run:
                stair["run_length_mm"] = round(float(tread_depth) * float(treads_per_run), 3)
                landing_width = float(stair.get("landing_width_mm") or 0)
                stair["total_run_mm"] = round(float(stair["run_length_mm"]) * run_count + landing_width, 3)
            stair["remarks"] = (
                str(stair.get("remarks") or "")
                + f" 踏步高采用同项目立面/详图可信值{project_riser_height:g}mm，覆盖平面误读值{current:g}mm。"
            ).strip()


def infer_project_stair_span(drawing_results: list[tuple[str, dict]]) -> tuple[int, str | None, str | None]:
    """Infer only from explicit floor titles; file codes such as F005 are drawing ids, not floors."""
    floor_numbers: list[int] = []
    for _drawing_name, result in drawing_results:
        notes = result.get("notes", {})
        if notes.get("drawing_type") != "architectural_plan":
            continue
        texts = [str(notes.get("drawing_title") or "")]
        for candidate in notes.get("drawing_title_candidates", []):
            if isinstance(candidate, dict):
                texts.append(str(candidate.get("raw_text") or ""))
                texts.append(str(candidate.get("text") or ""))
        for text in texts:
            if not re.search(r"[一二三四五六七八九十\d]+\s*层", text):
                continue
            number_value = floor_number_from_text(text)
            if number_value is not None:
                floor_numbers.append(number_value)
                break
    unique = sorted(set(floor_numbers))
    if len(unique) < 2:
        return 1, None, None
    start = unique[0]
    end = unique[-1]
    return max(1, end - start), f"{start}\u5c42", f"{end}\u5c42"


def level_span_from_labels(start_level: object, end_level: object) -> int | None:
    start = floor_number_from_text(str(start_level or ""))
    end = floor_number_from_text(str(end_level or ""))
    if start is None or end is None or end <= start:
        return None
    return end - start


def floor_number_from_text(text: str) -> int | None:
    match = re.search(r"([一二三四五六七八九十\d]+)\s*层|L\s*(\d+)|F\s*(\d+)", text, re.I)
    if not match:
        return None
    raw = match.group(1) or match.group(2) or match.group(3)
    return unicode_chinese_number(raw)


def unicode_chinese_number(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + digits.get(text[1:], 0)
    if "十" in text:
        left, right = text.split("十", 1)
        return digits.get(left, 0) * 10 + digits.get(right, 0)
    return digits.get(text)


def build_double_run_stair_candidate(text: str, entities: list[CadEntity], result: dict) -> StairCandidate | None:
    if not STAIR_TEXT_RE.search(text):
        return None

    geometry = infer_geometry_details(entities)
    stair_box = geometry.stair_box
    start, end, boundary, width_from_geometry, run_from_geometry, direction = geometry_parameters(stair_box, result)

    step_count = parse_step_count(text)
    text_tread_depth, text_riser_height = parse_tread_depth_and_riser(text)
    dimension_tread, dimension_riser = infer_dimension_step_sizes(entities, stair_box)
    tread_depth = first_number(text_tread_depth, dimension_tread, geometry.tread_depth_mm)
    riser_height = first_number(text_riser_height, dimension_riser, geometry.riser_height_mm)
    dimension_total_rise = infer_dimension_total_rise(entities, stair_box)
    total_rise = first_number(parse_total_rise(text), dimension_total_rise)
    start_level, end_level = parse_level_range(text)

    computed_total_risers = None
    if total_rise and riser_height:
        computed_total_risers = round(total_rise / riser_height)

    geometric_treads = geometry.number_of_treads
    geometric_risers = geometry.number_of_risers
    if computed_total_risers:
        total_risers = computed_total_risers
    elif step_count:
        total_risers = step_count * 2
    elif geometric_treads:
        total_risers = geometric_treads + 2
    else:
        total_risers = geometric_risers

    if step_count:
        risers_per_run = step_count
    elif total_risers:
        risers_per_run = round(total_risers / 2)
    else:
        risers_per_run = None

    number_of_treads = geometry.number_of_treads
    if number_of_treads is None and total_risers:
        number_of_treads = max(total_risers - 2, 0)
    treads_per_run = round(number_of_treads / 2) if number_of_treads else None
    run_length = tread_depth * treads_per_run if tread_depth and treads_per_run else None
    total_run = None
    if run_length:
        total_run = run_length * 2 + (geometry.landing_width_mm or 0)
    elif tread_depth and number_of_treads:
        total_run = tread_depth * number_of_treads
    elif run_from_geometry:
        total_run = run_from_geometry
    if total_rise is None and riser_height and total_risers:
        total_rise = riser_height * total_risers

    stair_width = first_number(geometry.width_mm, width_from_geometry)
    stairwell_width = geometry.stairwell_width_mm
    landing_length = first_number(geometry.landing_length_mm, stair_width)
    landing_width = first_number(geometry.landing_width_mm, stair_width)

    missing = []
    if not total_rise:
        missing.append("层高/总高")
    if not tread_depth:
        missing.append("踏步宽")
    if not riser_height:
        missing.append("踏步高")
    if not total_risers:
        missing.append("级数")
    if not stair_width:
        missing.append("梯段/平台宽度")
    if not stairwell_width:
        missing.append("stairwell width")
    if not boundary:
        missing.append("楼梯边界/楼梯洞口")

    remarks = build_remarks(step_count, computed_total_risers, geometric_risers, geometry)
    confidence = 0.78
    if geometry.source_segment_count:
        confidence += 0.06
    if dimension_tread or dimension_riser or dimension_total_rise:
        confidence += 0.04
    if missing:
        confidence -= min(0.35, 0.06 * len(missing))
        remarks += " 缺少：" + "、".join(missing) + "。"
    if drawing_type_is_detail_or_section(result):
        confidence += 0.06
    confidence = round(max(0.35, min(confidence, 0.92)), 3)

    return StairCandidate(
        id="STAIR0001",
        stair_type="double_run_stair",
        start_level=start_level,
        end_level=end_level,
        start=start,
        end=end,
        boundary_points=boundary,
        stairwell_opening_boundary=boundary,
        opening_required=bool(boundary),
        total_rise_mm=round(total_rise, 3) if total_rise else None,
        total_run_mm=round(total_run, 3) if total_run else None,
        width_mm=round(stair_width, 3) if stair_width else None,
        stairwell_width_mm=round(stairwell_width, 3) if stairwell_width else None,
        run_count=2,
        risers_per_run=int(risers_per_run) if risers_per_run else None,
        treads_per_run=int(treads_per_run) if treads_per_run else None,
        run_length_mm=round(run_length, 3) if run_length else None,
        landing_length_mm=round(landing_length, 3) if landing_length else None,
        landing_width_mm=round(landing_width, 3) if landing_width else None,
        riser_height_mm=round(riser_height, 3) if riser_height else None,
        tread_depth_mm=round(tread_depth, 3) if tread_depth else None,
        number_of_risers=int(total_risers) if total_risers else None,
        number_of_treads=int(number_of_treads) if number_of_treads else None,
        direction=direction,
        source="stair_detail_text_dimension_and_geometry",
        source_segment_count=geometry.source_segment_count,
        confidence=confidence,
        needs_review=bool(missing),
        remarks=remarks,
    )


def parse_step_count(text: str) -> int | None:
    match = STEP_COUNT_RE.search(text)
    return int(match.group(1)) if match else None


def parse_tread_depth_and_riser(text: str) -> tuple[float | None, float | None]:
    match = TREAD_RE.search(text)
    if match:
        first = float(match.group(1))
        second = float(match.group(2))
        return max(first, second), min(first, second)
    tread = regex_number(TREAD_WIDTH_RE, text)
    riser = regex_number(RISER_HEIGHT_RE, text)
    return tread, riser


def parse_total_rise(text: str) -> float | None:
    match = HEIGHT_RE.search(text)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "mm").lower()
    if unit in {"m", "米"} or value < 20:
        return value * 1000
    return value


def parse_level_range(text: str) -> tuple[str | None, str | None]:
    match = LEVEL_RANGE_RE.search(text)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def regex_number(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(text)
    return float(match.group(1)) if match else None


def infer_geometry_details(entities: list[CadEntity]) -> GeometryDetails:
    segments = extract_stair_segments(entities)
    if not segments:
        segments = extract_fallback_segments(entities)
    clusters = cluster_segments(segments) if segments else []
    cluster = choose_best_cluster(clusters) if clusters else []
    stair_box = bbox([point for seg in cluster for point in (seg.start, seg.end)]) if cluster else None
    tread_depth, riser_height, riser_count, tread_count = infer_step_pattern(cluster)
    horizontal_tread_count, horizontal_riser_count = infer_best_horizontal_counts(clusters)
    if horizontal_tread_count:
        tread_count = horizontal_tread_count
    if horizontal_riser_count:
        riser_count = horizontal_riser_count
    width, landing_length, landing_width = infer_platform_and_width(cluster)
    stairwell_width = infer_stairwell_width(cluster)
    dimension_landing_length, dimension_landing_width = infer_dimension_platform_sizes(entities)
    if dimension_landing_length and dimension_landing_width:
        landing_length = dimension_landing_length
        landing_width = dimension_landing_width
        width = width or dimension_landing_width
    return GeometryDetails(
        stair_box=stair_box,
        source_segment_count=len(cluster),
        tread_depth_mm=tread_depth,
        riser_height_mm=riser_height,
        number_of_risers=riser_count,
        number_of_treads=tread_count,
        width_mm=width,
        stairwell_width_mm=stairwell_width,
        landing_length_mm=landing_length,
        landing_width_mm=landing_width,
    )


def extract_stair_segments(entities: list[CadEntity]) -> list[SegmentInfo]:
    return [seg for ent in entities if is_stair_entity(ent) for seg in entity_segments(ent)]


def extract_fallback_segments(entities: list[CadEntity]) -> list[SegmentInfo]:
    segments: list[SegmentInfo] = []
    for ent in entities:
        if ent.type not in {"LINE", "LWPOLYLINE", "POLYLINE"}:
            continue
        if EXCLUDED_FALLBACK_LAYER_RE.search(ent.layer or ""):
            continue
        segments.extend(entity_segments(ent))
    return segments


def entity_segments(ent: CadEntity) -> list[SegmentInfo]:
    data = ent.data or {}
    segments: list[SegmentInfo] = []
    if ent.type == "LINE":
        start = point_tuple(data.get("start"))
        end = point_tuple(data.get("end"))
        if start and end:
            segments.append(SegmentInfo(start, end, ent.layer))
    elif ent.type in {"LWPOLYLINE", "POLYLINE"}:
        points = [point_tuple(item) for item in data.get("points", [])]
        clean_points = [item for item in points if item is not None]
        for a, b in zip(clean_points, clean_points[1:]):
            segments.append(SegmentInfo(a, b, ent.layer))
        if data.get("closed") and len(clean_points) > 2:
            segments.append(SegmentInfo(clean_points[-1], clean_points[0], ent.layer))
    return [seg for seg in segments if seg.length > 1.0]


def cluster_segments(segments: list[SegmentInfo], gap_tolerance: float = 1800.0) -> list[list[SegmentInfo]]:
    clusters: list[list[SegmentInfo]] = []
    for seg in sorted(segments, key=lambda item: (item.box[0], item.box[1])):
        for cluster in clusters:
            cluster_box = bbox([point for item in cluster for point in (item.start, item.end)])
            if boxes_gap(seg.box, cluster_box) <= gap_tolerance:
                cluster.append(seg)
                break
        else:
            clusters.append([seg])
    merged = True
    while merged:
        merged = False
        for i in range(len(clusters)):
            if merged:
                break
            box_i = bbox([point for item in clusters[i] for point in (item.start, item.end)])
            for j in range(i + 1, len(clusters)):
                box_j = bbox([point for item in clusters[j] for point in (item.start, item.end)])
                if boxes_gap(box_i, box_j) <= gap_tolerance:
                    clusters[i].extend(clusters.pop(j))
                    merged = True
                    break
    return clusters


def choose_best_cluster(clusters: list[list[SegmentInfo]]) -> list[SegmentInfo]:
    if not clusters:
        return []
    return max(clusters, key=cluster_score)


def cluster_score(cluster: list[SegmentInfo]) -> tuple[float, int]:
    axis = [seg for seg in cluster if seg.orientation in {"H", "V"}]
    short = [seg for seg in axis if 80 <= seg.length <= 650]
    vertical_short = [seg for seg in short if seg.orientation == "V"]
    horizontal_short = [seg for seg in short if seg.orientation == "H"]
    long_common = common_length([seg.length for seg in axis if 800 <= seg.length <= 2500])
    score = len(short) + min(len(vertical_short), len(horizontal_short)) * 2
    if long_common:
        score += 8
    if len(cluster) < 4:
        score -= 20
    return score, len(cluster)


def infer_step_pattern(segments: list[SegmentInfo]) -> tuple[float | None, float | None, int | None, int | None]:
    horizontals = [seg.length for seg in segments if seg.orientation == "H" and 80 <= seg.length <= 650]
    verticals = [seg.length for seg in segments if seg.orientation == "V" and 80 <= seg.length <= 650]
    tread = common_length(horizontals)
    riser = common_length(verticals)
    riser_count = count_close(verticals, riser) if riser else None
    tread_count = count_close(horizontals, tread) if tread else None
    if tread_count:
        return tread, riser, tread_count, tread_count
    return tread, riser, riser_count, riser_count


def infer_best_horizontal_counts(clusters: list[list[SegmentInfo]]) -> tuple[int | None, int | None]:
    candidates = [count_horizontal_steps(cluster) for cluster in clusters]
    candidates = [item for item in candidates if item != (None, None)]
    if not candidates:
        return None, None
    return max(candidates, key=lambda item: item[1] or item[0] or 0)


def count_horizontal_steps(segments: list[SegmentInfo]) -> tuple[int | None, int | None]:
    long_horizontals = [seg.length for seg in segments if seg.orientation == "H" and 700 <= seg.length <= 3000]
    long_common = common_length(long_horizontals)
    long_count = count_close(long_horizontals, long_common) if long_common else None
    if long_count and long_count >= 4:
        return max(long_count - 2, 0), long_count

    short_horizontals = [seg.length for seg in segments if seg.orientation == "H" and 80 <= seg.length <= 650]
    short_common = common_length(short_horizontals)
    short_count = count_close(short_horizontals, short_common) if short_common else None
    if short_count and short_count >= 2:
        return short_count, short_count + 2
    return None, None


def infer_platform_and_width(segments: list[SegmentInfo]) -> tuple[float | None, float | None, float | None]:
    platform_rectangles = infer_platform_rectangles(segments)
    if platform_rectangles:
        width, length = min(platform_rectangles, key=lambda item: item[0] * item[1])
        return width, length, width

    axis_lengths = [seg.length for seg in segments if seg.orientation in {"H", "V"}]
    common_long = common_length([length for length in axis_lengths if 700 <= length <= 3000])
    if not segments:
        return None, None, None
    seg_box = bbox([point for seg in segments for point in (seg.start, seg.end)])
    if not seg_box:
        return None, None, None
    min_x, min_y, max_x, max_y = seg_box
    block_width = max_x - min_x
    block_depth = max_y - min_y
    landing_length = min(block_width, block_depth)
    landing_width = min(block_width, block_depth)
    if common_long:
        return common_long, landing_length, landing_width
    return landing_width, landing_length, landing_width


def infer_platform_rectangles(segments: list[SegmentInfo]) -> list[tuple[float, float]]:
    """Find compact, closed, axis-aligned landing outlines in stair geometry.

    Step runs can make the overall bounding box much longer than the landing.
    A small closed rectangle is stronger evidence for the landing dimensions
    than that overall box, especially when the rectangle is approximately
    square and both sides are drawn as separate CAD segments.
    """
    horizontals = [seg for seg in segments if seg.orientation == "H" and 700 <= seg.length <= 3000]
    verticals = [seg for seg in segments if seg.orientation == "V" and 700 <= seg.length <= 3000]
    rectangles: list[tuple[float, float]] = []
    tolerance = 5.0
    for index, first in enumerate(horizontals):
        for second in horizontals[index + 1 :]:
            first_box = first.box
            second_box = second.box
            if abs(first_box[0] - second_box[0]) > tolerance or abs(first_box[2] - second_box[2]) > tolerance:
                continue
            height = abs(first_box[1] - second_box[1])
            width = (first_box[2] - first_box[0] + second_box[2] - second_box[0]) / 2.0
            if height < 700 or height > 3000 or min(width, height) <= 0:
                continue
            has_left_side = any(
                abs(seg.box[0] - first_box[0]) <= tolerance
                and abs(seg.box[1] - min(first_box[1], second_box[1])) <= tolerance
                and abs(seg.box[3] - max(first_box[3], second_box[3])) <= tolerance
                for seg in verticals
            )
            has_right_side = any(
                abs(seg.box[0] - first_box[2]) <= tolerance
                and abs(seg.box[1] - min(first_box[1], second_box[1])) <= tolerance
                and abs(seg.box[3] - max(first_box[3], second_box[3])) <= tolerance
                for seg in verticals
            )
            if not (has_left_side and has_right_side):
                continue
            short_side = min(width, height)
            long_side = max(width, height)
            if long_side / short_side <= 1.8:
                candidate = (round(short_side, 3), round(long_side, 3))
                if candidate not in rectangles:
                    rectangles.append(candidate)
    return rectangles


def infer_stairwell_width(segments: list[SegmentInfo]) -> float | None:
    candidates = stairwell_gap_candidates([seg for seg in segments if seg.orientation == "V"])
    candidates.extend(stairwell_gap_candidates([seg for seg in segments if seg.orientation == "H"]))
    candidates.extend(paired_run_gap_candidates([seg for seg in segments if seg.orientation == "H"]))
    candidates.extend(paired_run_gap_candidates([seg for seg in segments if seg.orientation == "V"]))
    candidates.extend(overlapping_platform_gap_candidates([seg for seg in segments if seg.orientation == "H"]))
    candidates.extend(overlapping_platform_gap_candidates([seg for seg in segments if seg.orientation == "V"]))
    if not candidates:
        return None
    return min(candidates)


def stairwell_gap_candidates(segments: list[SegmentInfo]) -> list[float]:
    long_segments = [seg for seg in segments if 700 <= seg.length <= 6000]
    if len(long_segments) < 4 or len(long_segments) > 12:
        return []
    if long_segments[0].orientation == "V":
        items = sorted((round((seg.start[0] + seg.end[0]) / 20.0) * 10.0, seg.box[1], seg.box[3]) for seg in long_segments)
    else:
        items = sorted((round((seg.start[1] + seg.end[1]) / 20.0) * 10.0, seg.box[0], seg.box[2]) for seg in long_segments)
    merged: list[tuple[float, float, float]] = []
    for pos, span_start, span_end in items:
        if merged and abs(pos - merged[-1][0]) <= 20:
            old_pos, old_start, old_end = merged[-1]
            merged[-1] = (old_pos, min(old_start, span_start), max(old_end, span_end))
        else:
            merged.append((pos, span_start, span_end))
    if len(merged) < 4:
        return []
    gaps: list[float] = []
    for left, right in zip(merged, merged[1:]):
        overlap = min(left[2], right[2]) - max(left[1], right[1])
        min_span = min(left[2] - left[1], right[2] - right[1])
        if min_span <= 0 or overlap / min_span < 0.55:
            continue
        gap = right[0] - left[0]
        if 100 <= gap <= 1200:
            gaps.append(round(gap, 3))
    if len(gaps) < 3:
        return []
    return [min(gaps)]


def paired_run_gap_candidates(segments: list[SegmentInfo]) -> list[float]:
    long_segments = [seg for seg in segments if 700 <= seg.length <= 3000]
    if len(long_segments) < 4:
        return []
    grouped: dict[float, list[SegmentInfo]] = {}
    for seg in long_segments:
        axis = ((seg.start[1] + seg.end[1]) / 2.0) if seg.orientation == "H" else ((seg.start[0] + seg.end[0]) / 2.0)
        key = round(axis / 20.0) * 20.0
        grouped.setdefault(key, []).append(seg)

    gaps: list[float] = []
    for items in grouped.values():
        if len(items) < 2:
            continue
        if items[0].orientation == "H":
            spans = sorted((seg.box[0], seg.box[2]) for seg in items)
        else:
            spans = sorted((seg.box[1], seg.box[3]) for seg in items)
        for left, right in zip(spans, spans[1:]):
            gap = right[0] - left[1]
            if 50 <= gap <= 800:
                gaps.append(round(gap, 3))
    common = common_length(gaps, precision=10.0)
    if common is None or count_close(gaps, common, tolerance=20.0) is None:
        return []
    if count_close(gaps, common, tolerance=20.0) < 3:
        return []
    return [common]


def overlapping_platform_gap_candidates(segments: list[SegmentInfo]) -> list[float]:
    long_segments = [seg for seg in segments if 700 <= seg.length <= 3000]
    if len(long_segments) < 2:
        return []
    gaps: list[float] = []
    for index, first in enumerate(long_segments):
        for second in long_segments[index + 1 :]:
            if first.orientation != second.orientation:
                continue
            if first.orientation == "H":
                overlap = min(first.box[2], second.box[2]) - max(first.box[0], second.box[0])
                min_span = min(first.box[2] - first.box[0], second.box[2] - second.box[0])
                gap = abs(((first.start[1] + first.end[1]) / 2.0) - ((second.start[1] + second.end[1]) / 2.0))
            else:
                overlap = min(first.box[3], second.box[3]) - max(first.box[1], second.box[1])
                min_span = min(first.box[3] - first.box[1], second.box[3] - second.box[1])
                gap = abs(((first.start[0] + first.end[0]) / 2.0) - ((second.start[0] + second.end[0]) / 2.0))
            if min_span <= 0 or overlap / min_span < 0.55:
                continue
            if 50 <= gap <= 800:
                gaps.append(round(gap, 3))
    if not gaps:
        return []
    common = common_length(gaps, precision=10.0)
    if common is None:
        return []
    return [common]


def infer_dimension_step_sizes(
    entities: list[CadEntity],
    stair_box: tuple[float, float, float, float] | None,
) -> tuple[float | None, float | None]:
    dims = [ent for ent in entities if ent.type == "DIMENSION" and dimension_near_box(ent, stair_box)]
    horizontal: list[float] = []
    vertical: list[float] = []
    for ent in dims:
        data = ent.data or {}
        measurement = float(data.get("measurement") or 0)
        if measurement <= 0 or measurement > 450:
            continue
        start = point_tuple(data.get("start"))
        end = point_tuple(data.get("end"))
        if not start or not end:
            continue
        if abs(start[0] - end[0]) >= abs(start[1] - end[1]):
            if 120 <= measurement <= 450:
                horizontal.append(measurement)
        else:
            if 80 <= measurement <= 250:
                vertical.append(measurement)
    return common_length(horizontal), common_length(vertical)


def infer_dimension_platform_sizes(entities: list[CadEntity]) -> tuple[float | None, float | None]:
    values: list[float] = []
    for ent in entities:
        if ent.type != "DIMENSION":
            continue
        measurement = float((ent.data or {}).get("measurement") or 0)
        if 700 <= measurement <= 5000:
            values.append(measurement)
    unique = sorted({round(value / 10.0) * 10.0 for value in values})
    if len(unique) < 2:
        return None, None
    return max(unique), min(unique)


def infer_dimension_total_rise(
    entities: list[CadEntity],
    stair_box: tuple[float, float, float, float] | None,
) -> float | None:
    values: list[float] = []
    for ent in entities:
        if ent.type != "DIMENSION" or not dimension_near_box(ent, stair_box):
            continue
        data = ent.data or {}
        measurement = float(data.get("measurement") or 0)
        if measurement < 1800 or measurement > 8000:
            continue
        start = point_tuple(data.get("start"))
        end = point_tuple(data.get("end"))
        if not start or not end:
            continue
        if abs(start[1] - end[1]) >= abs(start[0] - end[0]):
            values.append(measurement)
    if not values:
        return None
    return max(values)


def dimension_near_box(ent: CadEntity, stair_box: tuple[float, float, float, float] | None) -> bool:
    if stair_box is None:
        return True
    point = point_tuple((ent.data or {}).get("point")) or point_tuple((ent.data or {}).get("text_point"))
    if not point:
        return False
    min_x, min_y, max_x, max_y = stair_box
    margin = 2500.0
    return min_x - margin <= point[0] <= max_x + margin and min_y - margin <= point[1] <= max_y + margin


def common_length(lengths: list[float], precision: float = 10.0) -> float | None:
    if not lengths:
        return None
    counts = Counter(round(length / precision) * precision for length in lengths if length > 0)
    if not counts:
        return None
    value, count = counts.most_common(1)[0]
    if count < 2 and len(lengths) > 2:
        return None
    return float(value)


def count_close(values: list[float], target: float | None, tolerance: float = 15.0) -> int | None:
    if target is None:
        return None
    count = sum(1 for value in values if abs(value - target) <= tolerance)
    return count or None


def stair_geometry_bbox(entities: list[CadEntity]) -> tuple[float, float, float, float] | None:
    return infer_geometry_details(entities).stair_box


def is_stair_entity(ent: CadEntity) -> bool:
    haystack = f"{ent.layer} {ent.data.get('name', '') if ent.data else ''}"
    return bool(STAIR_LAYER_RE.search(haystack))


def point_tuple(value: object) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def boxes_gap(
    a: tuple[float, float, float, float] | None,
    b: tuple[float, float, float, float] | None,
) -> float:
    if a is None or b is None:
        return float("inf")
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    dx = max(bx1 - ax2, ax1 - bx2, 0)
    dy = max(by1 - ay2, ay1 - by2, 0)
    return hypot(dx, dy)


def geometry_parameters(
    stair_box: tuple[float, float, float, float] | None,
    result: dict,
) -> tuple[
    tuple[float, float] | None,
    tuple[float, float] | None,
    list[tuple[float, float]],
    float | None,
    float | None,
    str,
]:
    if stair_box is None:
        return None, None, [], None, None, "unknown"
    min_x, min_y, max_x, max_y = stair_box
    origin = result.get("coordinate_system", {}).get("origin", [0.0, 0.0])
    min_x -= float(origin[0])
    max_x -= float(origin[0])
    min_y -= float(origin[1])
    max_y -= float(origin[1])
    dx = max_x - min_x
    dy = max_y - min_y
    boundary = [
        round_pair((min_x, min_y)),
        round_pair((max_x, min_y)),
        round_pair((max_x, max_y)),
        round_pair((min_x, max_y)),
    ]
    if dx >= dy:
        start = (min_x, (min_y + max_y) / 2)
        end = (max_x, (min_y + max_y) / 2)
        width = dy
        run = dx
        direction = "east"
    else:
        start = ((min_x + max_x) / 2, min_y)
        end = ((min_x + max_x) / 2, max_y)
        width = dx
        run = dy
        direction = "north"
    if hypot(end[0] - start[0], end[1] - start[1]) <= 0:
        return None, None, boundary, None, None, "unknown"
    return round_pair(start), round_pair(end), boundary, width, run, direction


def first_number(*values: float | None) -> float | None:
    for value in values:
        if value is not None and value > 0:
            return float(value)
    return None


def build_remarks(
    step_count: int | None,
    computed_total_risers: int | None,
    geometric_risers: int | None,
    geometry: GeometryDetails,
) -> str:
    parts: list[str] = []
    if step_count:
        parts.append(f"文本标注每跑{step_count}级，按双跑楼梯处理。")
    if computed_total_risers:
        parts.append(f"由层高/踏步高推得总级数{computed_total_risers}。")
    if geometry.number_of_treads:
        parts.append(f"由有效踏步横线识别约{geometry.number_of_treads}个踏步。")
    if geometry.width_mm:
        parts.append(f"由常见长边识别平台/梯段宽度约{geometry.width_mm:g}。")
    if not parts:
        parts.append("楼梯参数需人工复核。")
    return " ".join(parts)


def round_pair(value: tuple[float, float]) -> tuple[float, float]:
    return round(value[0], 3), round(value[1], 3)


def drawing_type_is_detail_or_section(result: dict) -> bool:
    return result.get("notes", {}).get("drawing_type") in {"architectural_detail", "architectural_section"}
