from __future__ import annotations

from dataclasses import asdict, dataclass


FACADE_NAMES = {
    "south": "\u5357\u7acb\u9762",
    "north": "\u5317\u7acb\u9762",
    "east": "\u4e1c\u7acb\u9762",
    "west": "\u897f\u7acb\u9762",
}

MATCHABLE_KINDS = {"door", "window"}


@dataclass
class OpeningMatch:
    opening_kind: str
    plan_drawing: str
    plan_opening_id: str
    elevation_drawing: str
    elevation_opening_id: str
    facade: str
    score: float
    match_status: str
    plan_annotation: str | None
    elevation_annotation: str | None
    plan_width_mm: float | None
    elevation_width_mm: float | None
    plan_height_mm: float | None
    elevation_height_mm: float | None
    plan_projected_mm: float | None
    elevation_projected_mm: float | None
    position_delta_mm: float | None
    reason: str


def match_project_openings(drawing_results: list[tuple[str, dict]]) -> list[dict]:
    plan_openings = collect_plan_openings(drawing_results)
    elevation_openings = collect_elevation_openings(drawing_results)
    matches: list[OpeningMatch] = []
    used_elevation: set[tuple[str, str, str]] = set()

    for plan in plan_openings:
        candidates = [
            elev
            for elev in elevation_openings
            if elev.get("kind") == plan.get("kind")
            and elev.get("facade") == plan.get("facade")
            and (str(elev["kind"]), str(elev["drawing"]), str(elev["id"])) not in used_elevation
        ]
        scored = [(score_opening_pair(plan, elev), elev) for elev in candidates]
        scored = [(score, elev) for score, elev in scored if score[0] > 0]
        if not scored:
            fallback_candidates = [
                elev
                for elev in elevation_openings
                if elev.get("kind") == plan.get("kind")
                and (str(elev["kind"]), str(elev["drawing"]), str(elev["id"])) not in used_elevation
            ]
            scored = [(score_opening_pair(plan, elev, allow_facade_fallback=True), elev) for elev in fallback_candidates]
            scored = [(score, elev) for score, elev in scored if score[0] >= 0.68]
        if not scored:
            matches.append(unmatched_plan_opening(plan))
            continue

        scored.sort(key=lambda item: item[0][0], reverse=True)
        (score, reason, delta), best = scored[0]
        used_elevation.add((str(best["kind"]), str(best["drawing"]), str(best["id"])))
        matches.append(
            OpeningMatch(
                opening_kind=str(plan.get("kind")),
                plan_drawing=str(plan["drawing"]),
                plan_opening_id=str(plan["id"]),
                elevation_drawing=str(best["drawing"]),
                elevation_opening_id=str(best["id"]),
                facade=FACADE_NAMES.get(str(plan.get("facade")), str(plan.get("facade"))),
                score=round(score, 3),
                match_status="matched" if score >= 0.68 else "needs_review",
                plan_annotation=plan.get("annotation"),
                elevation_annotation=best.get("annotation"),
                plan_width_mm=plan.get("width"),
                elevation_width_mm=best.get("width"),
                plan_height_mm=plan.get("height"),
                elevation_height_mm=best.get("height"),
                plan_projected_mm=plan.get("projected"),
                elevation_projected_mm=best.get("projected"),
                position_delta_mm=delta,
                reason=reason,
            )
        )
    return [asdict(item) for item in matches]


def match_project_windows(drawing_results: list[tuple[str, dict]]) -> list[dict]:
    return [item for item in match_project_openings(drawing_results) if item.get("opening_kind") == "window"]


def match_project_doors(drawing_results: list[tuple[str, dict]]) -> list[dict]:
    return [item for item in match_project_openings(drawing_results) if item.get("opening_kind") == "door"]


def apply_cross_view_opening_enrichment(
    drawing_results: list[tuple[str, dict]],
    matches: list[dict] | None = None,
) -> int:
    """Copy elevation-derived opening size data back to matched plan openings."""
    matches = matches if matches is not None else match_project_openings(drawing_results)
    opening_index = index_openings(drawing_results)
    enriched = 0

    for match in matches:
        opening_kind = match.get("opening_kind")
        if opening_kind not in MATCHABLE_KINDS:
            continue
        match_status = str(match.get("match_status") or "")
        if opening_kind == "window" and match_status != "matched":
            continue
        if opening_kind == "door" and match_status not in {"matched", "needs_review"}:
            continue
        plan_key = (str(match.get("plan_drawing")), str(match.get("plan_opening_id")))
        elevation_key = (str(match.get("elevation_drawing")), str(match.get("elevation_opening_id")))
        plan_opening = opening_index.get(plan_key)
        elevation_opening = opening_index.get(elevation_key)
        if not plan_opening or not elevation_opening:
            continue

        changed = False
        sill_height = number(elevation_opening.get("sill_height_mm"))
        if sill_height is not None:
            previous = number(plan_opening.get("sill_height_mm"))
            if previous is not None and abs(previous - sill_height) > 1e-6:
                plan_opening["sill_height_plan_original"] = round(previous, 3)
            plan_opening["sill_height_mm"] = round(sill_height, 3)
            plan_opening["sill_height_source"] = "matched_elevation_opening"
            changed = True

        height = number(elevation_opening.get("height_mm") or elevation_opening.get("height"))
        if height is not None:
            previous = number(plan_opening.get("height_mm"))
            if previous is not None and abs(previous - height) > 1e-6:
                plan_opening["height_plan_original"] = round(previous, 3)
            plan_opening["height_mm"] = round(height, 3)
            plan_opening["height_source"] = (
                "matched_elevation_opening"
                if match_status == "matched"
                else "matched_elevation_opening_needs_review"
            )
            changed = True

        if opening_kind == "door":
            plan_opening.setdefault("sill_height_mm", 0)
            plan_opening.setdefault("sill_height_source", "door_default_floor_level")

        annotation = elevation_opening.get("annotation")
        if annotation and not plan_opening.get("annotation"):
            plan_opening["annotation"] = annotation
            plan_opening["annotation_source"] = (
                "matched_elevation_opening"
                if match_status == "matched"
                else "matched_elevation_opening_needs_review"
            )
            changed = True

        category = elevation_opening.get("component_category")
        if category and str(plan_opening.get("component_category") or "").lower() in {"", "unknown"}:
            plan_opening["component_category"] = category
            plan_opening["component_category_source"] = "matched_elevation_opening"
            changed = True

        if changed:
            plan_opening["matched_elevation_drawing"] = match.get("elevation_drawing")
            plan_opening["matched_elevation_opening_id"] = match.get("elevation_opening_id")
            plan_opening["cross_view_match_score"] = match.get("score")
            plan_opening["cross_view_match_reason"] = match.get("reason")
            plan_opening["cross_view_match_status"] = match_status
            enriched += 1

    return enriched


def index_openings(drawing_results: list[tuple[str, dict]]) -> dict[tuple[str, str], dict]:
    index: dict[tuple[str, str], dict] = {}
    for drawing_name, result in drawing_results:
        for opening in result.get("openings", []):
            opening_id = opening.get("id")
            if opening_id in {None, ""}:
                continue
            index[(str(drawing_name), str(opening_id))] = opening
    return index


def collect_plan_openings(drawing_results: list[tuple[str, dict]]) -> list[dict]:
    rows: list[dict] = []
    for drawing_name, result in drawing_results:
        if result.get("notes", {}).get("drawing_type") != "architectural_plan":
            continue
        for opening in result.get("openings", []):
            kind = opening.get("kind")
            if kind not in MATCHABLE_KINDS:
                continue
            facade = infer_plan_facade(opening, result)
            if not facade:
                continue
            rows.append(opening_match_row(drawing_name, opening, result, facade))
    return rows


def collect_elevation_openings(drawing_results: list[tuple[str, dict]]) -> list[dict]:
    rows: list[dict] = []
    for drawing_name, result in drawing_results:
        if result.get("notes", {}).get("drawing_type") != "architectural_elevation":
            continue
        facade = extract_facade_direction(result.get("notes", {}).get("drawing_title") or drawing_name)
        if not facade:
            continue
        for opening in result.get("openings", []):
            kind = opening.get("kind")
            if kind not in MATCHABLE_KINDS:
                continue
            rows.append(opening_match_row(drawing_name, opening, result, facade))
    return rows


def opening_match_row(drawing_name: str, opening: dict, result: dict, facade: str) -> dict:
    local = normalized_point(opening, result)
    drawing_type = result.get("notes", {}).get("drawing_type")
    return {
        "drawing": drawing_name,
        "id": opening.get("id"),
        "kind": opening.get("kind"),
        "facade": facade,
        "annotation": opening.get("annotation"),
        "width": number(opening.get("width")),
        "height": number(opening.get("height_mm") or opening.get("height")),
        "point": local,
        "projected": projected_coordinate(local, facade, drawing_type),
    }


def score_opening_pair(plan: dict, elev: dict, allow_facade_fallback: bool = False) -> tuple[float, str, float | None]:
    score = 0.22
    reasons = ["same kind"]
    if plan.get("facade") == elev.get("facade"):
        reasons.append("same facade")
    elif allow_facade_fallback:
        reasons.append("fallback facade")
    else:
        return 0.0, "different facade", None

    plan_mark = normalized_annotation(plan.get("annotation"))
    elev_mark = normalized_annotation(elev.get("annotation"))
    if plan_mark and elev_mark:
        if plan_mark == elev_mark:
            score += 0.42
            reasons.append("same annotation")
        elif plan_mark[:1] == elev_mark[:1]:
            score += 0.08
            reasons.append("same annotation type")

    width_delta = delta_number(plan.get("width"), elev.get("width"))
    if width_delta is not None:
        if width_delta <= 80:
            score += 0.2
            reasons.append("similar width")
        elif width_delta <= 200:
            score += 0.1
            reasons.append("near width")

    height_delta = delta_number(plan.get("height"), elev.get("height"))
    if height_delta is not None:
        if height_delta <= 120:
            score += 0.08
            reasons.append("similar height")
        elif height_delta <= 300:
            score += 0.04
            reasons.append("near height")

    position_delta = delta_number(plan.get("projected"), elev.get("projected"))
    if position_delta is not None:
        if position_delta <= 300:
            score += 0.2
            reasons.append("similar projected position")
        elif position_delta <= 900:
            score += 0.1
            reasons.append("near projected position")
        else:
            score -= 0.08

    return max(0.0, min(score, 1.0)), "; ".join(reasons), position_delta


def unmatched_plan_opening(plan: dict) -> OpeningMatch:
    kind = str(plan.get("kind"))
    return OpeningMatch(
        opening_kind=kind,
        plan_drawing=str(plan["drawing"]),
        plan_opening_id=str(plan["id"]),
        elevation_drawing="",
        elevation_opening_id="",
        facade=FACADE_NAMES.get(str(plan.get("facade")), str(plan.get("facade"))),
        score=0.0,
        match_status="unmatched",
        plan_annotation=plan.get("annotation"),
        elevation_annotation=None,
        plan_width_mm=plan.get("width"),
        elevation_width_mm=None,
        plan_height_mm=plan.get("height"),
        elevation_height_mm=None,
        plan_projected_mm=plan.get("projected"),
        elevation_projected_mm=None,
        position_delta_mm=None,
        reason=f"no elevation {kind} candidate on same facade",
    )


def infer_plan_facade(opening: dict, result: dict) -> str | None:
    host_id = opening.get("host_wall_id")
    walls = result.get("walls", [])
    host = next((wall for wall in walls if wall.get("id") == host_id), None)
    if host is None:
        return None
    wall_center = midpoint(host.get("local_start") or host.get("start"), host.get("local_end") or host.get("end"))
    if wall_center is None:
        return None
    wall_box = walls_bbox(walls)
    if wall_box is None:
        return None
    min_x, min_y, max_x, max_y = wall_box
    orientation = wall_orientation(host)
    if orientation == "H":
        return "south" if abs(wall_center[1] - min_y) <= abs(wall_center[1] - max_y) else "north"
    if orientation == "V":
        return "west" if abs(wall_center[0] - min_x) <= abs(wall_center[0] - max_x) else "east"
    return None


def projected_coordinate(point_value: tuple[float, float] | None, facade: str, drawing_type: object = None) -> float | None:
    if point_value is None:
        return None
    if drawing_type == "architectural_elevation":
        return point_value[0]
    if facade in {"south", "north"}:
        return point_value[0]
    if facade in {"east", "west"}:
        return point_value[1]
    return None


def normalized_point(opening: dict, result: dict) -> tuple[float, float] | None:
    if result.get("notes", {}).get("drawing_type") == "architectural_elevation":
        facade_local = point(opening.get("facade_local_point"))
        if facade_local is not None:
            return facade_local
    pt = point(opening.get("local_point") or opening.get("point"))
    if pt is None:
        return None
    coord = result.get("coordinate_system", {})
    frame = result.get("frame", {})
    if frame and coord.get("source") == "fallback_world_origin":
        return (pt[0] - float(frame.get("min_x", 0) or 0), pt[1] - float(frame.get("min_y", 0) or 0))
    return pt


def extract_facade_direction(text: str | None) -> str | None:
    if not text:
        return None
    low = str(text).lower()
    if "\u5357" in low or "south" in low:
        return "south"
    if "\u5317" in low or "north" in low:
        return "north"
    if "\u4e1c" in low or "east" in low:
        return "east"
    if "\u897f" in low or "west" in low:
        return "west"
    return None


def wall_orientation(wall: dict) -> str:
    start = point(wall.get("local_start") or wall.get("start"))
    end = point(wall.get("local_end") or wall.get("end"))
    if start is None or end is None:
        return "OTHER"
    dx = abs(end[0] - start[0])
    dy = abs(end[1] - start[1])
    if dx >= dy * 5:
        return "H"
    if dy >= dx * 5:
        return "V"
    return "OTHER"


def walls_bbox(walls: list[dict]) -> tuple[float, float, float, float] | None:
    pts: list[tuple[float, float]] = []
    for wall in walls:
        for key in ["local_start", "local_end", "start", "end"]:
            pt = point(wall.get(key))
            if pt is not None:
                pts.append(pt)
    if not pts:
        return None
    xs = [pt[0] for pt in pts]
    ys = [pt[1] for pt in pts]
    return min(xs), min(ys), max(xs), max(ys)


def midpoint(a: object, b: object) -> tuple[float, float] | None:
    pa = point(a)
    pb = point(b)
    if pa is None or pb is None:
        return None
    return ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)


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


def delta_number(a: object, b: object) -> float | None:
    na = number(a)
    nb = number(b)
    if na is None or nb is None:
        return None
    return abs(na - nb)


def normalized_annotation(value: object) -> str | None:
    if value in {None, ""}:
        return None
    return str(value).strip().upper()
