from __future__ import annotations

from pathlib import Path

from .dxf_parser import CadEntity
from .elevation_openings import replace_elevation_openings
from .floor_slabs import build_default_floor_slabs
from .frames import DrawingFrame
from .model_requirements import apply_requirement_enrichment
from .notes import analyze_text_and_notes
from .plan_analysis import analyze_plan
from .railing_recognition import recognize_railings
from .recognizer import recognize
from .relations import analyze_relations
from .stair_recognition import recognize_stairs
from .wall_runs import build_logical_wall_runs


def analyze_entities(
    entities: list[CadEntity],
    original_path: str | Path,
    parsed_dxf_path: str | Path,
    frame: DrawingFrame | None = None,
) -> dict:
    result = recognize(entities)
    if frame is not None:
        result["frame"] = frame.to_dict()
    result["notes"] = analyze_text_and_notes(entities, result["counts"])
    result["plan_summary"] = analyze_plan(entities, result)
    replace_elevation_openings(entities, result)
    refresh_opening_counts(result)
    add_stair_block_floor_openings(entities, result)
    apply_requirement_enrichment(result)
    recognize_stairs(entities, result)
    recognize_railings(entities, result)
    build_default_floor_slabs(result, entities)
    build_logical_wall_runs(result)
    refresh_derived_counts(result)
    result["relations"] = analyze_relations(entities, result)
    result["raw_geometry"] = summarize_raw_geometry(entities)
    result["input"] = {"original": str(original_path), "parsed_dxf": str(parsed_dxf_path)}
    return result


def add_stair_block_floor_openings(entities: list[CadEntity], result: dict) -> None:
    """Expose plan stair-block bounds as a separate stairwell-opening domain."""

    openings = result.setdefault("floor_openings", [])
    seen_centers = {
        tuple(round(float(value), 1) for value in opening.get("center", ())[:2])
        for opening in openings
        if isinstance(opening.get("center"), (list, tuple)) and len(opening["center"]) >= 2
    }
    for entity in entities:
        if entity.type != "INSERT" or not is_stair_block_reference(entity):
            continue
        bounds = entity.data.get("block_bounds")
        if not isinstance(bounds, (list, tuple)) or len(bounds) < 4:
            continue
        min_x, min_y, max_x, max_y = (float(value) for value in bounds[:4])
        width = max_x - min_x
        depth = max_y - min_y
        if width < 600 or depth < 600:
            continue
        center = (round((min_x + max_x) / 2.0, 3), round((min_y + max_y) / 2.0, 3))
        center_key = tuple(round(value, 1) for value in center)
        if center_key in seen_centers:
            continue
        boundary = [
            [round(min_x, 3), round(min_y, 3)],
            [round(max_x, 3), round(min_y, 3)],
            [round(max_x, 3), round(max_y, 3)],
            [round(min_x, 3), round(max_y, 3)],
        ]
        openings.append(
            {
                "id": f"FOP{len(openings) + 1:04d}",
                "opening_type": "stairwell_opening",
                "center": center,
                "width": round(width, 3),
                "depth": round(depth, 3),
                "area": round(width * depth, 3),
                "boundary_points": boundary,
                "layer": entity.layer or "",
                "confidence": 0.96,
                "source": "stair_block_bounds",
                "source_geometry_count": int(entity.data.get("block_geometry_count") or 1),
                "needs_review": False,
                "remarks": (
                    "Dedicated plan stair-block footprint. This opening is independent from generic "
                    "opening-layer voids and must not remove or replace adjacent walls."
                ),
            }
        )
        seen_centers.add(center_key)


def is_stair_block_reference(entity: CadEntity) -> bool:
    data = entity.data or {}
    text = " ".join(
        [
            str(entity.layer or ""),
            str(data.get("name") or ""),
            " ".join(str(value) for value in data.get("block_layers", []) or []),
        ]
    ).lower()
    return "stair" in text or "\u697c\u68af" in text


def refresh_opening_counts(result: dict) -> None:
    openings = result.get("openings", [])
    doors = [item for item in openings if item.get("kind") == "door"]
    windows = [item for item in openings if item.get("kind") == "window"]
    result.setdefault("counts", {})["openings"] = len(openings)
    plan_counts = result.setdefault("plan_summary", {}).setdefault("counts", {})
    plan_counts["doors"] = len(doors)
    plan_counts["windows"] = len(windows)
    plan_counts["openings_total"] = len(openings)


def refresh_derived_counts(result: dict) -> None:
    counts = result.setdefault("counts", {})
    counts["columns"] = len(result.get("columns", []))
    counts["floor_openings"] = len(result.get("floor_openings", []))
    counts["floors"] = len(result.get("floors", []))
    counts["stairs"] = len(result.get("stairs", []))
    counts["railings"] = len(result.get("railings", []))
    counts["parapets"] = len(result.get("parapets", []))


def summarize_raw_geometry(entities: list[CadEntity]) -> list[dict]:
    rows: list[dict] = []
    counters: dict[str, int] = {}
    for ent in entities:
        kind = raw_geometry_kind(ent.type)
        counters[kind] = counters.get(kind, 0) + 1
        raw_id = f"{kind}-{counters[kind]:06d}"
        row = {
            "raw_geometry_id": raw_id,
            "raw_type": ent.type,
            "layer": ent.layer or "0",
            "start_x": "",
            "start_y": "",
            "end_x": "",
            "end_y": "",
            "center_x": "",
            "center_y": "",
            "radius": "",
            "text": "",
            "block_name": "",
            "point_count": "",
        }
        data = ent.data or {}
        if ent.type == "LINE":
            start = data.get("start", (None, None))
            end = data.get("end", (None, None))
            row.update({"start_x": coord(start, 0), "start_y": coord(start, 1), "end_x": coord(end, 0), "end_y": coord(end, 1)})
        elif ent.type == "ARC":
            center = data.get("center", (None, None))
            row.update({"center_x": coord(center, 0), "center_y": coord(center, 1), "radius": data.get("radius", "")})
        elif ent.type == "CIRCLE":
            center = data.get("center", (None, None))
            row.update({"center_x": coord(center, 0), "center_y": coord(center, 1), "radius": data.get("radius", "")})
        elif ent.type in {"TEXT", "MTEXT"}:
            point = data.get("point", (None, None))
            row.update({"center_x": coord(point, 0), "center_y": coord(point, 1), "text": data.get("text", "")})
        elif ent.type == "DIMENSION":
            start = data.get("start", (None, None))
            end = data.get("end", (None, None))
            point = data.get("point", (None, None))
            row.update(
                {
                    "start_x": coord(start, 0),
                    "start_y": coord(start, 1),
                    "end_x": coord(end, 0),
                    "end_y": coord(end, 1),
                    "center_x": coord(point, 0),
                    "center_y": coord(point, 1),
                    "text": data.get("text", "") or data.get("measurement", ""),
                }
            )
        elif ent.type == "INSERT":
            point = data.get("point", (None, None))
            row.update({"center_x": coord(point, 0), "center_y": coord(point, 1), "block_name": data.get("name", "")})
        elif ent.type in {"LWPOLYLINE", "POLYLINE"}:
            points = data.get("points", [])
            row["point_count"] = len(points)
            if points:
                row.update({"start_x": coord(points[0], 0), "start_y": coord(points[0], 1), "end_x": coord(points[-1], 0), "end_y": coord(points[-1], 1)})
        rows.append(row)
    return rows


def raw_geometry_kind(entity_type: str) -> str:
    if entity_type == "LINE":
        return "LINE"
    if entity_type == "ARC":
        return "ARC"
    if entity_type in {"LWPOLYLINE", "POLYLINE"}:
        return "POLYLINE"
    if entity_type == "INSERT":
        return "BLOCK"
    return entity_type.upper() or "UNKNOWN"


def coord(value: object, index: int) -> object:
    if isinstance(value, (list, tuple)) and len(value) > index:
        return value[index]
    return ""
