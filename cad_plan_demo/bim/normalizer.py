from __future__ import annotations

import ast
import csv
import json
import re
from pathlib import Path

from .schema import empty_standard_model


DEFAULT_LEVEL_HEIGHT_MM = 3000.0
DEFAULT_WINDOW_SILL_HEIGHT_MM = 900.0

NON_SPATIAL_VIEW_TYPES = {"detail", "elevation", "section"}
DETAIL_ENRICHMENT_FIELDS = {
    "walls": ("thickness_mm", "height_mm", "material", "material_layers", "finish_materials"),
    "doors": ("width_mm", "height_mm", "thickness_mm", "material", "semantic_type", "design_type", "type"),
    "windows": ("width_mm", "height_mm", "sill_height_mm", "thickness_mm", "material", "semantic_type", "design_type", "type"),
    "stairs": (
        "total_rise_mm", "total_run_mm", "width_mm", "stairwell_width_mm", "run_count",
        "risers_per_run", "treads_per_run", "run_length_mm", "landing_length_mm",
        "landing_width_mm", "riser_height_mm", "tread_depth_mm", "number_of_risers",
        "number_of_treads", "material",
    ),
    "railings": ("height_mm", "thickness_mm", "material"),
    "roofs": ("thickness_mm", "slope", "material", "material_layers"),
    "parapets": ("height_mm", "thickness_mm", "material"),
    "slabs": ("thickness_mm", "material", "material_layers"),
}


def normalize_workbook(workbook: dict[str, list[dict[str, str]]], notes: str, source_excel: str, source_notes: str) -> dict:
    model = empty_standard_model(project_name=Path(source_excel).stem)
    model["project"]["source_excel"] = source_excel
    model["project"]["source_notes"] = source_notes
    model["project"]["design_note_summary"] = _summarize_notes(notes)

    levels = [_normalize_level(row, i) for i, row in enumerate(workbook.get("levels", []), start=1)]
    floor_analysis = _infer_levels_from_standard_package(Path(source_excel), levels)
    if floor_analysis.get("levels"):
        levels = floor_analysis["levels"]
    if not levels:
        levels = [
            {
                "id": "L001",
                "type": "level",
                "name": "Level 1",
                "elevation_mm": 0.0,
                "source": "system_default:no_level_sheet",
                "confidence": 0.3,
                "review_status": "needs_review",
                "notes": "No levels were provided in the input workbook.",
            }
        ]
    model["components"]["levels"] = levels
    model["floor_analysis"] = floor_analysis

    level_lookup = _level_lookup(levels)

    model["components"]["grids"] = [_normalize_grid(row, i) for i, row in enumerate(workbook.get("grids", []), start=1)]
    model["components"]["columns"] = [_normalize_column(row, i, levels, level_lookup) for i, row in enumerate(workbook.get("columns", []), start=1)]
    model["components"]["walls"] = [_normalize_wall(row, i, levels, level_lookup) for i, row in enumerate(workbook.get("walls", []), start=1)]
    model["components"]["slabs"] = [_normalize_slab(row, i, levels, level_lookup) for i, row in enumerate(workbook.get("slabs", []), start=1)]
    model["rooms"] = [_normalize_room(row, i, levels, level_lookup) for i, row in enumerate(workbook.get("rooms", []), start=1)]
    model["components"]["floor_openings"] = [
        _normalize_floor_opening(row, i, levels, level_lookup)
        for i, row in enumerate(workbook.get("floor_openings", []), start=1)
    ]
    model["components"]["doors"] = [_normalize_opening(row, i, levels, level_lookup, "door") for i, row in enumerate(workbook.get("doors", []), start=1)]
    model["components"]["windows"] = [_normalize_opening(row, i, levels, level_lookup, "window") for i, row in enumerate(workbook.get("windows", []), start=1)]
    model["components"]["stairs"] = [_normalize_generic_component(row, i, levels, level_lookup, "stairs", "ST", "stair") for i, row in enumerate(workbook.get("stairs", []), start=1)]
    model["components"]["railings"] = [_normalize_generic_component(row, i, levels, level_lookup, "railings", "RA", "railing") for i, row in enumerate(workbook.get("railings", []), start=1)]
    model["components"]["roofs"] = [_normalize_generic_component(row, i, levels, level_lookup, "roofs", "RF", "flat_roof") for i, row in enumerate(workbook.get("roofs", []), start=1)]
    model["components"]["parapets"] = [_normalize_parapet(row, i, levels, level_lookup) for i, row in enumerate(workbook.get("parapets", []), start=1)]
    _apply_floor_relationships(model)
    _deduplicate_model_components(model, Path(source_excel))
    return model


def _normalize_level(row: dict[str, str], index: int) -> dict:
    name = _pick(row, "name", "level", "level_name", "楼层", default=f"Level {index}")
    elevation = _to_float(_pick(row, "elevation_mm", "elevation", "标高", default=""))
    floor_height = _to_float(_pick(row, "floor_height_mm", "floor_height", "height", default=""))
    review = _review_status(row, "ready" if elevation is not None else "needs_review")
    return {
        "id": _pick(row, "id", "level_id", default=f"L{index:03d}"),
        "type": _pick(row, "type", default="level"),
        "name": name,
        "elevation_mm": elevation if elevation is not None else 0.0,
        "floor_height_mm": floor_height,
        "source": _pick(row, "source", default="excel:levels"),
        "confidence": _confidence(row, 0.9 if elevation is not None else 0.4),
        "review_status": review,
        "notes": _pick(row, "notes", default="" if elevation is not None else "Missing elevation; defaulted to 0 for preview only."),
    }


def _normalize_grid(row: dict[str, str], index: int) -> dict:
    start = _point_from_row(row, "start", z=0.0)
    end = _point_from_row(row, "end", z=0.0)
    ready = start is not None and end is not None
    return {
        "id": _pick(row, "id", "grid_id", "element_id", default=f"G{index:03d}"),
        "type": _pick(row, "type", "grid_type", default="grid"),
        "name": _pick(row, "name", "grid_name", default=f"G{index}"),
        "drawing_id": _pick(row, "drawing_id", default=""),
        "start": start or _point(0, 0, 0),
        "end": end or _point(0, 0, 0),
        "source": _pick(row, "source", default="excel:grids"),
        "confidence": _confidence(row, 0.85 if ready else 0.35),
        "review_status": _review_status(row, "ready" if ready else "needs_review"),
        "notes": _pick(row, "notes", default="" if ready else "Missing grid start or end point."),
    }


def _normalize_wall(row: dict[str, str], index: int, levels: list[dict], level_lookup: dict[str, str]) -> dict:
    start = _point_from_row(row, "start", z=0.0)
    end = _point_from_row(row, "end", z=0.0)
    base_level = _resolve_level_name(_pick(row, "base_level", "level", "level_id", default=levels[0]["name"]), level_lookup)
    height = _to_float(_pick(row, "height_mm", "height", default=""))
    top_level = _pick(row, "top_level", default="")
    ready = start is not None and end is not None and bool(base_level) and (height is not None or bool(top_level))
    return {
        "id": _pick(row, "id", "element_id", default=f"W{index:04d}"),
        "type": _pick(row, "type", "wall_type", default="Generic Wall"),
        "drawing_id": _pick(row, "drawing_id", default=""),
        "base_level": base_level,
        "top_level": top_level or None,
        "height_mm": height,
        "thickness_mm": _to_float(_pick(row, "thickness_mm", "normalized_width", "thickness", "width", default="")),
        "material": _pick(row, "material", default=None),
        "is_exterior": _to_bool(_pick(row, "is_exterior", "exterior", default="")),
        "start": start or _point(0, 0, 0),
        "end": end or _point(0, 0, 0),
        "source": _pick(row, "source", default="excel:walls"),
        "confidence": _confidence(row, 0.85 if ready else 0.45),
        "review_status": _review_status(row, "ready" if ready else "needs_review"),
        "notes": _pick(row, "notes", "remarks", default="" if ready else "Wall needs start/end coordinates and height or top level."),
    }


def _normalize_column(row: dict[str, str], index: int, levels: list[dict], level_lookup: dict[str, str]) -> dict:
    location = (
        _point_from_row(row, "center", z=0.0)
        or _point_from_row(row, "location", z=0.0)
        or _point_from_row(row, "point", z=0.0)
    )
    level = _resolve_level_name(_pick(row, "level", "level_id", default=levels[0]["name"]), level_lookup)
    base_z = _to_float(_pick(row, "base_z_mm", "base_z", "base_elevation", default=""))
    top_z = _to_float(_pick(row, "top_z_mm", "top_z", "top_elevation", default=""))
    height = _to_float(_pick(row, "height_mm", "height", default=""))
    if height is None and base_z is not None and top_z is not None:
        height = round(top_z - base_z, 3)
    width = _to_float(_pick(row, "width_mm", "width", default=""))
    depth = _to_float(_pick(row, "depth_mm", "depth", default=""))
    diameter = _to_float(_pick(row, "diameter_mm", "diameter", default=""))
    has_profile = diameter is not None or (width is not None and depth is not None)
    ready = location is not None and bool(level) and has_profile
    return {
        "id": _pick(row, "id", "element_id", default=f"C{index:04d}"),
        "type": _pick(row, "type", "column_type", default="Generic Column"),
        "drawing_id": _pick(row, "drawing_id", default=""),
        "level": level,
        "top_level": _pick(row, "top_level", default=None),
        "location": location or _point(0, 0, 0),
        "base_z_mm": base_z,
        "top_z_mm": top_z,
        "height_mm": height,
        "width_mm": width,
        "depth_mm": depth,
        "diameter_mm": diameter,
        "rotation_angle": _to_float(_pick(row, "rotation_angle", "rotation", "angle", default="")),
        "grid_reference": _pick(row, "grid_reference", default=""),
        "material": _pick(row, "material", default=None),
        "source": _pick(row, "source", default="excel:columns"),
        "confidence": _confidence(row, 0.86 if ready else 0.4),
        "review_status": _review_status(row, "ready" if ready else "needs_review"),
        "notes": _pick(row, "notes", "remarks", default="" if ready else "Column needs center point, level, and width/depth or diameter."),
    }


def _normalize_slab(row: dict[str, str], index: int, levels: list[dict], level_lookup: dict[str, str]) -> dict:
    boundary = _parse_boundary(_pick(row, "boundary", "boundary_points", "points", default=""))
    ready = len(boundary) >= 3
    slab_type = _pick(row, "type", "floor_type", "slab_type", default="Generic Floor")
    default_role = slab_type if slab_type in {"balcony_slab", "canopy_slab", "cantilever_slab"} else "regular_floor_slab"
    return {
        "id": _pick(row, "id", "element_id", default=f"SL{index:04d}"),
        "type": slab_type,
        "slab_role": _pick(row, "slab_role", default=default_role),
        "source_layer": _pick(row, "source_layer", "layer", default=""),
        "drawing_id": _pick(row, "drawing_id", default=""),
        "level": _resolve_level_name(_pick(row, "level", "level_id", default=levels[0]["name"]), level_lookup),
        "boundary_id": _pick(row, "boundary_id", default=""),
        "boundary": boundary,
        "thickness_mm": _to_float(_pick(row, "thickness_mm", "thickness", default="")),
        "thickness_source": _pick(row, "thickness_source", default="input"),
        "thickness_status": _pick(row, "thickness_status", default="input_value_pending_review"),
        "elevation_mm": _to_float(_pick(row, "elevation_mm", "elevation", default="")),
        "area_mm2": _to_float(_pick(row, "area_mm2", "area", default="")),
        "material": _pick(row, "material", default=None),
        "is_closed_boundary": _to_bool(_pick(row, "is_closed_boundary", default="")),
        "opening_ids": _parse_id_list(_pick(row, "opening_ids", "opening_id", default="")),
        "opening_count": _to_float(_pick(row, "opening_count", default="")),
        "source": _pick(row, "source", default="excel:slabs"),
        "confidence": _confidence(row, 0.8 if ready else 0.35),
        "review_status": _review_status(row, "ready" if ready else "needs_review"),
        "notes": _pick(row, "notes", "remarks", default="" if ready else "Slab boundary needs at least three points."),
    }


def _normalize_room(row: dict[str, str], index: int, levels: list[dict], level_lookup: dict[str, str]) -> dict:
    location = (
        _point_from_row(row, "center", z=0.0)
        or _point_from_row(row, "location", z=0.0)
        or _point_from_row(row, "point", z=0.0)
    )
    boundary = _parse_boundary(_pick(row, "boundary", "boundary_points", "points", default=""))
    level = _resolve_level_name(_pick(row, "level", "level_id", default=levels[0]["name"]), level_lookup)
    name = _pick(row, "room_name", "name", default="")
    area = _to_float(_pick(row, "area_mm2", "area", default=""))
    perimeter = _to_float(_pick(row, "perimeter_mm", "perimeter", default=""))
    ready = bool(name) and bool(level) and area is not None and len(boundary) >= 3
    return {
        "id": _pick(row, "id", "room_id", "element_id", default=f"ROOM{index:04d}"),
        "name": name,
        "number": _pick(row, "room_number", "number", default=""),
        "usage": _pick(row, "usage", "function", "room_usage", default=""),
        "drawing_id": _pick(row, "drawing_id", default=""),
        "level": level,
        "location": location,
        "boundary_id": _pick(row, "boundary_id", default=""),
        "boundary": boundary,
        "area_mm2": area,
        "perimeter_mm": perimeter,
        "adjacent_wall_ids": [],
        "source": _pick(row, "source", default="excel:rooms"),
        "confidence": _confidence(row, 0.85 if ready else 0.4),
        "review_status": _review_status(row, "ready" if ready else "needs_review"),
        "notes": _pick(row, "notes", "remarks", default="" if ready else "Room needs a name, level, area, and closed boundary before Revit room creation."),
    }


def _normalize_floor_opening(row: dict[str, str], index: int, levels: list[dict], level_lookup: dict[str, str]) -> dict:
    boundary = _parse_boundary(_pick(row, "boundary", "boundary_points", "points", default=""))
    location = (
        _point_from_row(row, "center", z=0.0)
        or _point_from_row(row, "location", z=0.0)
        or _point_from_row(row, "point", z=0.0)
    )
    level = _resolve_level_name(_pick(row, "level", "level_id", default=levels[0]["name"]), level_lookup)
    host_floor = _pick(row, "host_floor_id", "floor_id", default="")
    width = _to_float(_pick(row, "width_mm", "width", default=""))
    depth = _to_float(_pick(row, "depth_mm", "depth", default=""))
    ready = len(boundary) >= 3 and bool(host_floor)
    raw_type = _pick(row, "type", "opening_type", default="floor_opening")
    source = _pick(row, "source", default="excel:floor_openings")
    notes = _pick(row, "notes", "remarks", default="" if ready else "Floor opening needs host floor and closed boundary.")
    related_stair_id = _pick(row, "related_stair_id", "stair_id", default="")
    semantic, semantic_status, semantic_source = _classify_floor_opening_semantic(
        raw_type,
        source,
        notes,
        related_stair_id,
    )
    return {
        "id": _pick(row, "id", "opening_id", "element_id", default=f"FOP{index:04d}"),
        "type": raw_type,
        "source_opening_type": raw_type,
        "opening_origin_domain": _floor_opening_origin_domain(source, raw_type),
        "opening_semantic": semantic,
        "opening_semantic_status": semantic_status,
        "opening_semantic_source": semantic_source,
        "stair_candidate_allowed": semantic == "stairwell_opening" and semantic_status == "confirmed",
        "drawing_id": _pick(row, "drawing_id", default=""),
        "level": level,
        "host_floor_id": host_floor or None,
        "location": location or _point(0, 0, 0),
        "boundary": boundary,
        "width_mm": width,
        "depth_mm": depth,
        "area_mm2": _to_float(_pick(row, "area_mm2", "area", default="")),
        "source": source,
        "related_stair_id": related_stair_id or None,
        "confidence": _confidence(row, 0.82 if ready else 0.4),
        "review_status": _review_status(row, "ready" if ready else "needs_review"),
        "notes": notes,
    }


def _classify_floor_opening_semantic(
    raw_type: str,
    source: str,
    notes: str,
    related_stair_id: str,
) -> tuple[str, str, str]:
    text = " ".join((str(raw_type), str(source), str(notes))).lower()
    loft_tokens = ("loft", "open to below", "double height", "void", "\u6311\u7a7a", "\u4e0a\u7a7a", "\u4e2d\u7a7a")
    shaft_tokens = ("shaft", "\u4e95\u9053", "\u7535\u68af\u4e95", "\u7ba1\u4e95")
    stair_tokens = ("stairwell", "stair_", "stairs", "\u697c\u68af")
    has_loft = any(token in text for token in loft_tokens)
    has_shaft = any(token in text for token in shaft_tokens)
    has_stair = bool(related_stair_id) or any(token in text for token in stair_tokens)
    detected = sum((has_loft, has_shaft, has_stair))
    if detected > 1:
        return "semantic_conflict", "blocked", "conflicting_type_source_or_notes"
    if has_loft:
        return "loft_opening", "confirmed", "type_source_or_notes"
    if has_shaft:
        return "shaft_opening", "confirmed", "type_source_or_notes"
    if has_stair:
        return "stairwell_opening", "confirmed", "type_source_or_related_stair"
    return "general_floor_opening", "unresolved", "generic_opening_evidence"


def _floor_opening_origin_domain(source: str, raw_type: str) -> str:
    source_text = str(source or "").strip().lower()
    type_text = str(raw_type or "").strip().lower()
    if source_text == "hole_layer_rectangle_with_foldline":
        return "generic_opening_layer"
    if source_text == "stair_block_bounds":
        return "stair_plan_block"
    if source_text == "stair_layer_bounds":
        return "stair_layer_geometry"
    if source_text in {"stair_boundary", "stair_boundary_projected_to_host_floor"}:
        return "stair_detail_derived"
    if source_text.startswith("spatial_agent:stair_") or "stairwell" in type_text:
        return "stair_spatial_topology"
    return "unclassified_floor_opening"


def _normalize_opening(row: dict[str, str], index: int, levels: list[dict], level_lookup: dict[str, str], kind: str) -> dict:
    location = (
        _point_from_row(row, "location", z=0.0)
        or _point_from_row(row, "point", z=0.0)
        or _point_from_row(row, "center", z=0.0)
    )
    width = _to_float(_pick(row, "width_mm", "width", default=""))
    height = _to_float(_pick(row, "height_mm", "height", default=""))
    height_source = _pick(row, "height_source", default="")
    if height is not None and not height_source:
        height_source = f"cad_{kind}s_csv"
    host = _pick(row, "host_wall_id", "wall_id", default="")
    ready = location is not None and width is not None and height is not None and bool(host)
    prefix = "D" if kind == "door" else "WIN"
    classification = _parse_classification_input(_pick(row, "classification_input", default=""))
    mark = _opening_mark_from_row(row, classification, kind)
    mark_info = _parse_opening_mark(mark, kind)
    mark_width = mark_info.get("width_mm")
    mark_height = mark_info.get("height_mm")
    if mark_width is not None and (width is None or abs(width - mark_width) > 1e-6):
        width = mark_width
    if mark_height is not None and (height is None or abs(height - mark_height) > 1e-6):
        had_height = height is not None
        height = mark_height
        height_source = "opening_mark_override" if had_height else "opening_mark"
    ready = location is not None and width is not None and height is not None and bool(host)
    semantic = _opening_semantic_from_row(kind, row, classification, mark_info, width)
    geometry_type = _pick(row, f"{kind}_type", "type", default="")
    item = {
        "id": _pick(row, "id", "element_id", default=f"{prefix}{index:04d}"),
        "type": semantic["semantic_type"] if semantic["semantic_type"] != "unresolved" else (geometry_type or f"Generic {kind.title()}"),
        "recognized_type": semantic["semantic_type"],
        "design_type": semantic["semantic_type"],
        "geometry_type": geometry_type,
        "opening_mark": mark,
        "type_code": mark or None,
        "opening_mark_prefix": mark_info.get("prefix", ""),
        "opening_mark_width_mm": mark_info.get("width_mm"),
        "opening_mark_height_mm": mark_info.get("height_mm"),
        "opening_mark_source": mark_info.get("source", ""),
        "category_source": semantic["source"],
        "category_evidence": semantic["evidence"],
        "mechanical_category": _pick(row, "mechanical_category", default=""),
        "mechanical_category_source": _pick(row, "mechanical_category_source", default=""),
        "mechanical_category_confidence": _to_float(_pick(row, "mechanical_category_confidence", default="")),
        "needs_ai_classification": _to_bool(_pick(row, "needs_ai_classification", default="")),
        "classification_input": classification,
        "source_layers": classification.get("source_layers", []),
        "drawing_id": _pick(row, "drawing_id", default=""),
        "level": _resolve_level_name(_pick(row, "level", "level_id", default=levels[0]["name"]), level_lookup),
        "host_wall_id": host or None,
        "host_wall_run_id": _pick(row, "host_wall_run_id", "wall_run_id", default=None),
        "distance_from_host_start_mm": _to_float(_pick(row, "distance_from_host_start", "distance_from_run_start", default="")),
        "location": location or _point(0, 0, 0),
        "width_mm": width,
        "height_mm": height,
        "height_source": height_source or (classification.get("dimensions", {}).get("height_source", "") if isinstance(classification.get("dimensions"), dict) else ""),
        "height_evidence": f"CAD {kind.title()}s.csv Height field" if height_source == f"cad_{kind}s_csv" else "",
        "sill_height_mm": _to_float(_pick(row, "sill_height_mm", "sill_height", "sill", default="")) if kind == "window" else None,
        "sill_height_source": _pick(row, "sill_height_source", default=classification.get("dimensions", {}).get("sill_height_source", "") if isinstance(classification.get("dimensions"), dict) else ""),
        "material": _pick(row, "material", default=None),
        "semantic_class": kind,
        "semantic_type": semantic["semantic_type"],
        "semantic_type_label": _opening_semantic_label(kind, semantic["semantic_type"]),
        "semantic_type_source": semantic["source"],
        "semantic_type_confidence": semantic["confidence"],
        "semantic_type_needs_review": semantic["needs_review"],
        "semantic_type_reason": semantic["reason"],
        "source": _pick(row, "source", default=f"excel:{kind}s"),
        "confidence": _confidence(row, 0.82 if ready else 0.4),
        "review_status": _review_status(row, "ready" if ready else "needs_review"),
        "notes": _pick(row, "notes", "remarks", default="" if ready else f"{kind.title()} needs host wall, location, width and height."),
    }
    if kind == "door":
        direction = _normalize_open_direction(_pick(row, "opening_direction", "open_direction", "swing_direction", default=""))
        swing_angle = _to_float(_pick(row, "swing_angle", "swing_angle_deg", "opening_angle", default=""))
        panel_start = _point_from_row(row, "panel_start", z=0.0)
        panel_end = _point_from_row(row, "panel_end", z=0.0)
        swing_source = _pick(row, "swing_source", default="")
        swing_side = _normalize_handing(_pick(row, "swing_side", "handing", "door_hand", default=""))
        item.update(
            {
                "open_direction": direction,
                "swing_angle_deg": swing_angle,
                "swing_side": swing_side,
                "handing": swing_side,
                "swing_source": swing_source or ("existing_csv" if direction != "unknown" or swing_angle is not None else "unresolved"),
                "swing_evidence": _door_swing_evidence(direction, swing_angle, row),
                "swing_confidence": _to_float(_pick(row, "swing_confidence", default=""))
                or _door_swing_confidence(direction, swing_angle, row),
                "swing_needs_review": direction == "unknown",
                "panel_start": panel_start,
                "panel_end": panel_end,
                "panel_thickness_mm": _to_float(_pick(row, "panel_thickness", "panel_thickness_mm", default="")),
                "panel_wall_angle_deg": _to_float(_pick(row, "panel_wall_angle", "panel_wall_angle_deg", default="")),
            }
        )
        if direction == "unknown":
            item["notes"] = _append_note(item.get("notes", ""), "Door swing direction is missing and should be checked against the CAD door symbol.")
    return item


def _parse_classification_input(value: str) -> dict:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _opening_mark_from_row(row: dict[str, str], classification: dict, kind: str) -> str:
    keys = (f"{kind}_mark", "mark", "type_mark", "annotation")
    for key in keys:
        value = _pick(row, key, default="")
        cleaned = _clean_opening_mark(value)
        if cleaned:
            return cleaned
    for raw in classification.get("nearby_raw_geometry", []) or []:
        if not isinstance(raw, dict):
            continue
        cleaned = _clean_opening_mark(raw.get("text", ""))
        if cleaned:
            return cleaned
    cleaned = _clean_opening_mark(classification.get("annotation", ""))
    return cleaned


def _clean_opening_mark(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\\[Pp]", "", text)
    text = text.replace("\\", "").strip()
    match = re.search(r"[A-Za-z\u4e00-\u9fff]+[-_]?\d{3,4}(?:[-_]?\d{2,4})?", text)
    return match.group(0).upper().replace("-", "").replace("_", "") if match else ""


def _parse_opening_mark(mark: str, kind: str) -> dict:
    text = str(mark or "").strip().upper()
    if not text:
        return {"prefix": "", "width_mm": None, "height_mm": None, "source": ""}
    match = re.match(r"([A-Z\u4e00-\u9fff]+)(\d{2})(\d{2})$", text)
    if not match:
        match = re.match(r"([A-Z\u4e00-\u9fff]+)(\d{3,4})(\d{3,4})$", text)
    if not match:
        return {"prefix": re.sub(r"\d+", "", text), "width_mm": None, "height_mm": None, "source": "mark"}
    prefix, width_token, height_token = match.groups()
    width = _opening_dimension_from_token(width_token)
    height = _opening_dimension_from_token(height_token)
    return {"prefix": prefix, "width_mm": width, "height_mm": height, "source": "opening_mark"}


def _opening_dimension_from_token(token: str) -> float | None:
    if not token:
        return None
    value = int(token)
    if len(token) == 2:
        return float(value * 100)
    return float(value)


def _opening_semantic_from_row(kind: str, row: dict[str, str], classification: dict, mark_info: dict, width: float | None) -> dict:
    candidates = [
        _pick(row, "final_category", default=""),
        _pick(row, f"{kind}_category", default=""),
        _pick(row, "mechanical_category", default=""),
        classification.get("mechanical_category_candidate", ""),
        mark_info.get("prefix", ""),
        _pick(row, f"{kind}_type", "type", default=""),
    ]
    for candidate in candidates:
        semantic = _normalize_opening_semantic_candidate(kind, candidate)
        if semantic != "unresolved":
            return {
                "semantic_type": semantic,
                "source": _opening_semantic_source(candidate, row, classification, mark_info),
                "confidence": _opening_semantic_confidence(row, classification, semantic),
                "needs_review": _to_bool(_pick(row, "needs_ai_classification", default="")) is True and _opening_semantic_confidence(row, classification, semantic) < 0.75,
                "evidence": _opening_semantic_evidence(candidate, row, classification, mark_info),
                "reason": "Door/window semantic type was read from the standardized recognition output, block/mark text, or mechanical category.",
            }

    if kind == "door" and width is not None:
        semantic = "double_swing_door" if width >= 1200 else "single_swing_door"
        return {
            "semantic_type": semantic,
            "source": "width_rule_fallback",
            "confidence": 0.62,
            "needs_review": True,
            "evidence": f"width_mm={width:g}",
            "reason": "No explicit door type text was found; width was used only as a low-confidence fallback.",
        }

    return {
        "semantic_type": "unresolved",
        "source": "unresolved",
        "confidence": 0.0,
        "needs_review": True,
        "evidence": _opening_semantic_evidence("", row, classification, mark_info),
        "reason": "No reliable controlled door/window type evidence was found.",
    }


def _normalize_opening_semantic_candidate(kind: str, value) -> str:
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    compact = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", text)
    if kind == "door":
        if compact in {"single_swing_door", "singleswingdoor", "single_swing", "singleleafswingdoor", "d", "m"}:
            return "single_swing_door"
        if compact in {"double_swing_door", "doubleswingdoor", "double_swing", "doubleleafswingdoor", "dm", "双开门", "双扇平开门"}:
            return "double_swing_door"
        if compact in {"sliding_door", "slidingdoor", "slide_door", "tlm", "推拉门"} or "推拉" in compact or compact.startswith("tlm"):
            return "sliding_door"
        if "quarter_arc" in text or "swing" in text or "平开" in compact:
            return "single_swing_door"
    else:
        if compact in {"casement_window", "casementwindow", "pc", "平开窗"} or "平开" in compact or compact.startswith("pc"):
            return "casement_window"
        if compact in {"sliding_window", "slidingwindow", "slidewindow", "tc", "tlc", "推拉窗"} or "推拉" in compact or "sliding" in text or compact.startswith(("tc", "tlc")):
            return "sliding_window"
    return "unresolved"


def _opening_semantic_source(candidate, row: dict[str, str], classification: dict, mark_info: dict) -> str:
    candidate_text = str(candidate or "").strip()
    if candidate_text and candidate_text == _pick(row, "final_category", default=""):
        return "final_category"
    if candidate_text and candidate_text == _pick(row, "mechanical_category", default=""):
        return "mechanical_category"
    if candidate_text and candidate_text == classification.get("mechanical_category_candidate", ""):
        return "classification_input"
    if candidate_text and candidate_text == mark_info.get("prefix", ""):
        return "opening_mark"
    return "existing_csv"


def _opening_semantic_confidence(row: dict[str, str], classification: dict, semantic: str) -> float:
    values = [
        _to_float(_pick(row, "mechanical_category_confidence", default="")),
        _to_float(classification.get("mechanical_category_confidence")),
        _confidence(row, 0.0),
    ]
    best = max((value for value in values if value is not None), default=0.0)
    if semantic != "unresolved" and best <= 0:
        best = 0.7
    return round(min(max(best, 0.0), 0.95), 3)


def _opening_semantic_evidence(candidate, row: dict[str, str], classification: dict, mark_info: dict) -> str:
    pieces = []
    if candidate:
        pieces.append(f"category={candidate}")
    mark = _pick(row, "door_mark", "window_mark", "mark", default="")
    if mark:
        pieces.append(f"mark={mark}")
    if mark_info.get("width_mm") or mark_info.get("height_mm"):
        pieces.append(f"mark_size={mark_info.get('width_mm') or ''}x{mark_info.get('height_mm') or ''}")
    annotation = classification.get("annotation", "")
    if annotation and annotation != mark:
        pieces.append(f"annotation={annotation}")
    source = classification.get("recognition_source", "")
    if source:
        pieces.append(f"recognition_source={source}")
    return "; ".join(str(piece) for piece in pieces if piece)


def _opening_semantic_label(kind: str, semantic_type: str) -> str:
    if kind == "door":
        return {
            "single_swing_door": "单扇平开门",
            "double_swing_door": "双扇平开门",
            "sliding_door": "推拉门",
        }.get(semantic_type, "未确定门类型")
    return {
        "casement_window": "平开窗",
        "sliding_window": "推拉窗",
    }.get(semantic_type, "未确定窗类型")


def _normalize_generic_component(
    row: dict[str, str],
    index: int,
    levels: list[dict],
    level_lookup: dict[str, str],
    group: str,
    prefix: str,
    default_type: str,
) -> dict:
    boundary = _parse_boundary(_pick(row, "boundary", "boundary_points", "points", default=""))
    start = _point_from_row(row, "start", z=0.0)
    end = _point_from_row(row, "end", z=0.0)
    location = (
        _point_from_row(row, "location", z=0.0)
        or _point_from_row(row, "center", z=0.0)
        or _point_from_row(row, "point", z=0.0)
    )
    has_geometry = bool(boundary) or (start is not None and end is not None) or location is not None
    item = {
        "id": _pick(row, "id", "element_id", default=f"{prefix}{index:04d}"),
        "name": _pick(row, "name", "element_name", default=""),
        "type": _pick(row, "type", f"{default_type}_type", "stair_type", "railing_type", "roof_type", default=default_type),
        "drawing_id": _pick(row, "drawing_id", default=""),
        "level": _resolve_level_name(_pick(row, "level", "level_id", "start_level_id", default=levels[0]["name"]), level_lookup),
        "start_level": _resolve_level_name(_pick(row, "start_level", "start_level_id", default=""), level_lookup),
        "end_level": _resolve_level_name(_pick(row, "end_level", "end_level_id", default=""), level_lookup),
        "location": location or _point(0, 0, 0),
        "start": start,
        "end": end,
        "boundary_id": _pick(row, "boundary_id", default=""),
        "boundary": boundary,
        "width_mm": _to_float(_pick(row, "width_mm", "width", "stairwell_width", "landing_width", default="")),
        "height_mm": _to_float(_pick(row, "height_mm", "height", "total_rise", default="")),
        "thickness_mm": _to_float(_pick(row, "thickness_mm", "thickness", default="")),
        "elevation_mm": _to_float(_pick(row, "elevation_mm", "elevation", default="")),
        "area_mm2": _to_float(_pick(row, "area_mm2", "area", default="")),
        "material": _pick(row, "material", default=None),
        "source": _pick(row, "source", default=f"excel:{group}"),
        "confidence": _confidence(row, 0.75 if has_geometry else 0.35),
        "review_status": _review_status(row, "ready" if has_geometry else "needs_review"),
        "notes": _pick(row, "notes", "remarks", default="" if has_geometry else f"{group} needs at least a location, line, or boundary."),
    }
    if group == "stairs":
        item.update(
            {
                "stairwell_opening_id": _pick(row, "stairwell_opening_id", default=""),
                "stairwell_opening_boundary": _parse_boundary(_pick(row, "stairwell_opening_boundary", default="")),
                "opening_required": _to_bool(_pick(row, "opening_required", default="")),
                "total_rise_mm": _to_float(_pick(row, "total_rise", "total_rise_mm", default="")),
                "total_run_mm": _to_float(_pick(row, "total_run", "total_run_mm", default="")),
                "stairwell_width_mm": _to_float(_pick(row, "stairwell_width", "stairwell_width_mm", default="")),
                "run_count": _to_float(_pick(row, "run_count", default="")),
                "risers_per_run": _to_float(_pick(row, "risers_per_run", default="")),
                "treads_per_run": _to_float(_pick(row, "treads_per_run", default="")),
                "run_length_mm": _to_float(_pick(row, "run_length", "run_length_mm", default="")),
                "landing_length_mm": _to_float(_pick(row, "landing_length", "landing_length_mm", default="")),
                "landing_width_mm": _to_float(_pick(row, "landing_width", "landing_width_mm", default="")),
                "riser_height_mm": _to_float(_pick(row, "riser_height", "riser_height_mm", default="")),
                "tread_depth_mm": _to_float(_pick(row, "tread_depth", "tread_depth_mm", default="")),
                "number_of_risers": _to_float(_pick(row, "number_of_risers", default="")),
                "number_of_treads": _to_float(_pick(row, "number_of_treads", default="")),
                "direction": _pick(row, "direction", default=""),
                "source_geometry_count": _to_float(_pick(row, "source_geometry_count", default="")),
            }
        )
        _apply_stair_semantics(item, levels)
    elif group == "railings":
        item.update(
            {
                "railing_role": _pick(row, "railing_role", "railing_type", default="guard_railing"),
                "source_layer": _pick(row, "source_layer", "layer", default=""),
                "height_source": _pick(row, "height_source", default="input"),
                "height_status": _pick(row, "height_status", default="input_value"),
                "related_stair_id": _pick(row, "related_stair_id", "host_stair_id", default=""),
                "distance_to_stairwell_mm": _to_float(_pick(row, "distance_to_stairwell", "distance_to_stairwell_mm", default="")),
                "source_geometry_count": _to_float(_pick(row, "source_geometry_count", default="")),
            }
        )
    elif group == "roofs":
        item.update(
            {
                "slope": _to_float(_pick(row, "slope", default="")),
                "drainage_type": _pick(row, "drainage_type", default=""),
            }
        )
    return item


def _normalize_parapet(row: dict[str, str], index: int, levels: list[dict], level_lookup: dict[str, str]) -> dict:
    """Keep CAD Z as evidence, while giving Revit an explicit roof-relative base offset."""
    item = _normalize_generic_component(row, index, levels, level_lookup, "parapets", "PP", "roof_edge_parapet")
    base_level = _resolve_level_name(_pick(row, "revit_base_level", "base_level", "level", "level_id", default=item.get("level", "")), level_lookup)
    level_elevation = _to_float(next((level.get("elevation_mm") for level in levels if level.get("name") == base_level), None))
    cad_base_z = _point_z(item.get("start"))
    height = _to_float(item.get("height_mm"))
    bottom_offset = _to_float(_pick(row, "bottom_relative_elevation_mm", "bottom_relative_elevation", default=""))
    if bottom_offset is None:
        bottom_offset = round(cad_base_z - level_elevation, 3) if cad_base_z is not None and level_elevation is not None else 0.0

    item.update(
        {
            "host_roof_id": _pick(row, "host_roof_id", default=""),
            "revit_base_level": base_level or None,
            "bottom_relative_elevation_mm": bottom_offset,
            "top_relative_elevation_mm": round(bottom_offset + height, 3) if height is not None else None,
            "revit_bottom_elevation_mm": round(level_elevation + bottom_offset, 3) if level_elevation is not None else None,
            "revit_top_elevation_mm": round(level_elevation + bottom_offset + height, 3) if level_elevation is not None and height is not None else None,
            "cad_base_elevation_mm": cad_base_z,
            "height_source": _pick(row, "height_source", default="cad_or_default"),
            "vertical_binding_status": "ready" if base_level and level_elevation is not None and height is not None else "needs_review",
        }
    )
    if cad_base_z is not None and item["revit_bottom_elevation_mm"] is not None and abs(cad_base_z - item["revit_bottom_elevation_mm"]) > 10.0:
        item["vertical_binding_status"] = "needs_review"
        item["notes"] = _append_note(item.get("notes", ""), "CAD base Z conflicts with the resolved Revit base level and relative offset.")
    return item


def _apply_stair_semantics(item: dict, levels: list[dict]) -> None:
    start_z = _point_z(item.get("start"))
    end_z = _point_z(item.get("end"))
    total_rise = _to_float(item.get("total_rise_mm") or item.get("height_mm"))
    if total_rise is None and start_z is not None and end_z is not None:
        total_rise = round(end_z - start_z, 3)
        item["total_rise_mm"] = total_rise
        item["height_mm"] = total_rise

    start_level = _nearest_level_by_elevation(levels, start_z)
    end_level = _nearest_level_by_elevation(levels, end_z)
    if start_level and not item.get("start_level"):
        item["start_level"] = start_level.get("name", "")
    if end_level and not item.get("end_level"):
        item["end_level"] = end_level.get("name", "")
    if start_level:
        item["level"] = item.get("level") or start_level.get("name", "")
        item["start_level_elevation_mm"] = start_level.get("elevation_mm")
    if end_level:
        item["end_level_elevation_mm"] = end_level.get("elevation_mm")

    review_reasons = _stair_review_reasons(item, start_z, end_z, total_rise, levels)
    item["stair_height_source"] = "existing_csv_or_cad_detail"
    item["stair_height_evidence"] = _stair_height_evidence(item, start_z, end_z)
    item["stair_needs_review"] = bool(review_reasons)
    item["stair_review_reason"] = " | ".join(review_reasons)
    item["height_source"] = item.get("height_source") or item["stair_height_source"]
    item["height_evidence"] = item.get("height_evidence") or item["stair_height_evidence"]
    item["height_confidence"] = item.get("height_confidence") or item.get("confidence", "")
    item["height_needs_review"] = bool(review_reasons)
    item["height_reason"] = item.get("height_reason") or item["stair_review_reason"]
    item["height_completion_status"] = "needs_review" if review_reasons else "checked_or_completed"
    if review_reasons:
        item["review_status"] = "needs_review"
        item["notes"] = _append_note(item.get("notes", ""), "Stair vertical semantics need review: " + item["stair_review_reason"])


def _stair_review_reasons(
    item: dict,
    start_z: float | None,
    end_z: float | None,
    total_rise: float | None,
    levels: list[dict],
) -> list[str]:
    reasons: list[str] = []
    if total_rise is None:
        reasons.append("missing total_rise_mm")
    if start_z is not None and end_z is not None and total_rise is not None and abs((end_z - start_z) - total_rise) > 50:
        reasons.append("total_rise_mm does not match end_z-start_z")

    max_level = max((_to_float(level.get("elevation_mm")) or 0.0 for level in levels), default=0.0)
    if end_z is not None and max_level and end_z > max_level + 500:
        reasons.append("end_z exceeds highest known level elevation")
    if total_rise is not None and max_level and total_rise > max_level + 500:
        reasons.append("total_rise_mm exceeds known building height")

    riser = _to_float(item.get("riser_height_mm"))
    tread = _to_float(item.get("tread_depth_mm"))
    if riser is None:
        reasons.append("missing riser_height_mm")
    elif not 100 <= riser <= 220:
        reasons.append("riser_height_mm outside typical stair range")
    if tread is None:
        reasons.append("missing tread_depth_mm")
    elif not 200 <= tread <= 400:
        reasons.append("tread_depth_mm outside typical stair range")

    risers = _to_float(item.get("number_of_risers"))
    if total_rise is not None and riser is not None and risers is not None and risers > 0:
        expected_riser = total_rise / risers
        if abs(expected_riser - riser) > 20:
            reasons.append("riser_height_mm does not match total_rise_mm/number_of_risers")

    run_count = _to_float(item.get("run_count"))
    risers_per_run = _to_float(item.get("risers_per_run"))
    if run_count is not None and risers_per_run is not None and risers is not None:
        if abs(run_count * risers_per_run - risers) > 1:
            reasons.append("run_count * risers_per_run does not match number_of_risers")

    if item.get("opening_required") and not item.get("stairwell_opening_boundary"):
        reasons.append("opening_required but missing stairwell_opening_boundary")
    if _to_float(item.get("stairwell_width_mm")) is None:
        reasons.append("missing stairwell_width_mm")
    return reasons


def _stair_height_evidence(item: dict, start_z: float | None, end_z: float | None) -> str:
    pieces = []
    if start_z is not None:
        pieces.append(f"start_z={start_z:g}")
    if end_z is not None:
        pieces.append(f"end_z={end_z:g}")
    for key in ("total_rise_mm", "number_of_risers", "riser_height_mm", "tread_depth_mm"):
        value = item.get(key)
        if value not in (None, ""):
            pieces.append(f"{key}={value:g}" if isinstance(value, (int, float)) else f"{key}={value}")
    return "; ".join(pieces)


def _point_z(value) -> float | None:
    if not isinstance(value, dict):
        return None
    return _to_float(value.get("z"))


def _nearest_level_by_elevation(levels: list[dict], elevation: float | None, tolerance: float = 100.0) -> dict | None:
    if elevation is None:
        return None
    candidates = []
    for level in levels:
        level_elevation = _to_float(level.get("elevation_mm"))
        if level_elevation is None:
            continue
        candidates.append((abs(level_elevation - elevation), level))
    if not candidates:
        return None
    distance, level = min(candidates, key=lambda item: item[0])
    return level if distance <= tolerance else None


def _normalize_open_direction(value: str) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "e": "east",
        "east": "east",
        "右": "east",
        "向右": "east",
        "w": "west",
        "west": "west",
        "左": "west",
        "向左": "west",
        "n": "north",
        "north": "north",
        "上": "north",
        "向上": "north",
        "s": "south",
        "south": "south",
        "下": "south",
        "向下": "south",
        "left": "left",
        "right": "right",
        "double": "double",
        "双开": "double",
        "sliding": "sliding",
        "推拉": "sliding",
        "revolving": "revolving",
    }
    return aliases.get(text, "unknown")


def _normalize_handing(value: str) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "left": "left",
        "left_hand": "left",
        "lh": "left",
        "左手": "left",
        "right": "right",
        "right_hand": "right",
        "rh": "right",
        "右手": "right",
        "double": "double",
        "双开": "double",
    }
    return aliases.get(text, "")


def _door_swing_evidence(direction: str, swing_angle: float | None, row: dict[str, str]) -> str:
    parts = []
    if direction != "unknown":
        parts.append(f"Opening_Direction={direction}")
    if swing_angle is not None:
        parts.append(f"Swing_Angle={swing_angle:g}")
    source = _pick(row, "source", "door_type", "type", default="")
    if source:
        parts.append(f"source={source}")
    return "; ".join(parts)


def _door_swing_confidence(direction: str, swing_angle: float | None, row: dict[str, str]) -> float:
    base = _confidence(row, 0.0)
    if direction == "unknown" and swing_angle is None:
        return 0.0
    if direction != "unknown" and swing_angle is not None:
        return max(base, 0.82)
    return max(base, 0.68)


def _apply_floor_relationships(model: dict) -> None:
    floor_analysis = model.get("floor_analysis", {})
    assignments = {
        item.get("drawing_id", ""): item
        for item in floor_analysis.get("drawing_floor_assignments", [])
        if item.get("drawing_id") and item.get("assigned_base_level")
    }
    if not assignments:
        return

    for wall in model.get("components", {}).get("walls", []):
        assignment = assignments.get(wall.get("drawing_id", ""))
        if not assignment:
            continue
        floor_height = _to_float(assignment.get("floor_height_mm"))
        wall["base_level"] = assignment.get("assigned_base_level")
        wall["top_level"] = assignment.get("assigned_top_level") or wall.get("top_level")
        wall["floor_number"] = assignment.get("assigned_floor_number", "")
        wall["floor_name"] = assignment.get("assigned_floor_name", "")
        if floor_height is not None and _wall_height_is_default(wall):
            wall["height_mm"] = floor_height
            wall["notes"] = _append_note(wall.get("notes", ""), f"height_mm replaced by inferred floor height {floor_height:g} from elevation marks.")
        wall["floor_assignment_source"] = assignment.get("reason", "")
        wall["floor_assignment_review_status"] = assignment.get("review_status", "")

    for group in ("columns", "doors", "windows", "slabs", "floor_openings", "stairs", "railings", "roofs", "parapets"):
        for item in model.get("components", {}).get(group, []):
            assignment = assignments.get(item.get("drawing_id", ""))
            if not assignment:
                continue
            if group == "slabs":
                item["level"] = assignment.get("assigned_base_level")
            else:
                item["level"] = assignment.get("assigned_base_level")
            item["floor_number"] = assignment.get("assigned_floor_number", "")
            item["floor_name"] = assignment.get("assigned_floor_name", "")
            item["floor_assignment_source"] = assignment.get("reason", "")
            item["floor_assignment_review_status"] = assignment.get("review_status", "")


def _deduplicate_model_components(model: dict, source_path: Path) -> None:
    package = _standard_package_root(source_path)
    csv_root = _package_csv_root(package) if package else None
    drawings = _read_csv_rows(csv_root / "Drawings.csv") if csv_root else []
    drawing_info = {
        row.get("drawing_id", ""): {
            "drawing_name": row.get("drawing_name", ""),
            "drawing_type": row.get("drawing_type", ""),
            "view_semantic_role": row.get("view_semantic_role", ""),
            "coordinate_authority": row.get("coordinate_authority", ""),
            "detail_subject": row.get("detail_subject", ""),
            "parent_drawing_id": row.get("parent_drawing_id", ""),
        }
        for row in drawings
    }
    for info in drawing_info.values():
        if _is_stair_plan_drawing_name(info.get("drawing_name", "")):
            info["drawing_type"] = "detail"
        drawing_type = str(info.get("drawing_type", ""))
        info["view_semantic_role"] = info.get("view_semantic_role") or _view_semantic_role(drawing_type)
        info["coordinate_authority"] = info.get("coordinate_authority") or _coordinate_authority(drawing_type)
        info["detail_subject"] = info.get("detail_subject") or _detail_subject_from_name(info.get("drawing_name", ""), drawing_type)
    report: list[dict] = []
    before = _component_counts(model)

    for group, items in model.get("components", {}).items():
        if not isinstance(items, list):
            continue
        for item in items:
            info = drawing_info.get(item.get("drawing_id", ""), {})
            item["drawing_name"] = info.get("drawing_name", "")
            item["drawing_type"] = info.get("drawing_type", "")
            item["view_semantic_role"] = info.get("view_semantic_role") or _view_semantic_role(item["drawing_type"])
            item["coordinate_authority"] = info.get("coordinate_authority") or _coordinate_authority(item["drawing_type"])
            item["detail_subject"] = info.get("detail_subject", "")
            item["parent_drawing_id"] = info.get("parent_drawing_id", "")

    _apply_cross_view_detail_fusion(model, drawing_info, report)
    _apply_wall_spatial_roles(model)
    apply_opening_vertical_bindings(model)
    model["components"]["grids"] = _dedupe_grids(model.get("components", {}).get("grids", []), report)
    for group in ("columns", "walls", "slabs", "floor_openings", "doors", "windows", "stairs", "railings", "roofs", "parapets"):
        model["components"][group] = _remove_non_plan_projection_components(
            group,
            model.get("components", {}).get(group, []),
            report,
        )
    walls, wall_replacements = _dedupe_same_position_walls(
        model.get("components", {}).get("walls", []),
        report,
    )
    model["components"]["walls"] = walls
    _remap_wall_references(model, wall_replacements)
    model["wall_recheck"] = {
        "status": "passed" if not wall_replacements else "passed_after_deduplication",
        "duplicate_walls_removed": len(wall_replacements),
        "replacement_wall_ids": wall_replacements,
        "method": "same_level_collinear_axis_and_full_shorter_span_overlap",
    }
    model["components"]["floor_openings"] = _dedupe_same_source_floor_openings(
        model.get("components", {}).get("floor_openings", []),
        report,
    )
    model["components"]["slabs"] = _dedupe_same_level_slabs(
        model.get("components", {}).get("slabs", []),
        model.get("components", {}).get("floor_openings", []),
        report,
    )

    after = _component_counts(model)
    model["deduplication"] = {
        "method": "prefer_floor_plan_components",
        "summary": {
            "before": before,
            "after": after,
            "removed_total": sum(max(0, before.get(group, 0) - after.get(group, 0)) for group in before),
        },
        "removed_components": report,
    }


def _dedupe_same_source_floor_openings(items: list[dict], report: list[dict]) -> list[dict]:
    """Remove duplicate records without merging different opening source domains."""

    kept: list[dict] = []
    by_key: dict[tuple, dict] = {}
    for item in items or []:
        boundary_key = _floor_opening_boundary_key(item.get("boundary"))
        if not boundary_key:
            kept.append(item)
            continue
        key = (
            str(item.get("drawing_id") or ""),
            str(item.get("level") or ""),
            str(item.get("source") or "").strip().lower(),
            str(item.get("opening_origin_domain") or "").strip().lower(),
            boundary_key,
        )
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = item
            kept.append(item)
            continue
        for field in ("host_floor_id", "opening_semantic", "opening_semantic_status", "related_stair_id"):
            if not existing.get(field) and item.get(field):
                existing[field] = item[field]
        existing.setdefault("duplicate_source_ids", []).append(item.get("id", ""))
        report.append(
            _dedupe_report_row(
                group="floor_openings",
                item=item,
                duplicate_of=str(existing.get("id", "")),
                reason="Same drawing, source domain, level, and boundary describe one physical floor opening.",
            )
        )
    return kept


def _dedupe_same_level_slabs(
    items: list[dict],
    openings: list[dict],
    report: list[dict],
) -> list[dict]:
    referenced_ids = {
        str(opening.get("host_floor_id") or "")
        for opening in openings or []
        if opening.get("host_floor_id")
    }
    groups: dict[tuple, list[dict]] = {}
    passthrough: list[dict] = []
    for item in items or []:
        boundary_key = _floor_opening_boundary_key(item.get("boundary"))
        if not boundary_key:
            passthrough.append(item)
            continue
        key = (
            str(item.get("level") or item.get("base_level") or ""),
            str(item.get("slab_role") or "regular_floor_slab"),
            boundary_key,
        )
        groups.setdefault(key, []).append(item)

    kept = list(passthrough)
    replacement_ids: dict[str, str] = {}
    for group_items in groups.values():
        winner = max(
            group_items,
            key=lambda item: (
                1 if str(item.get("id") or "") in referenced_ids else 0,
                float(item.get("confidence") or 0.0),
                str(item.get("id") or ""),
            ),
        )
        kept.append(winner)
        for duplicate in group_items:
            if duplicate is winner:
                continue
            duplicate_id = str(duplicate.get("id") or "")
            winner_id = str(winner.get("id") or "")
            if duplicate_id and winner_id:
                replacement_ids[duplicate_id] = winner_id
            winner.setdefault("duplicate_source_ids", []).append(duplicate_id)
            report.append(
                _dedupe_report_row(
                    group="slabs",
                    item=duplicate,
                    duplicate_of=winner_id,
                    reason="Same level, slab role, and boundary describe one physical Revit floor.",
                )
            )
    for opening in openings or []:
        host_id = str(opening.get("host_floor_id") or "")
        if host_id in replacement_ids:
            opening["host_floor_id"] = replacement_ids[host_id]
    return sorted(kept, key=lambda item: str(item.get("id") or ""))


def _floor_opening_boundary_key(boundary) -> tuple[tuple[float, float], ...]:
    if not isinstance(boundary, list) or len(boundary) < 3:
        return ()
    points = []
    for point in boundary:
        if not isinstance(point, dict):
            return ()
        x = _to_float(point.get("x"))
        y = _to_float(point.get("y"))
        if x is None or y is None:
            return ()
        points.append((round(x, 1), round(y, 1)))
    return tuple(sorted(points))


def _apply_window_elevation_information(model: dict) -> None:
    windows = model.get("components", {}).get("windows", [])
    if not isinstance(windows, list):
        return

    elevation_windows = [
        item for item in windows
        if item.get("drawing_type") in {"elevation", "section"} and _to_float(item.get("sill_height_mm")) is not None
    ]
    if not elevation_windows:
        level_elevations = _level_elevation_lookup(model)
        for item in windows:
            sill = _to_float(item.get("sill_height_mm"))
            if sill is None:
                _apply_default_plan_window_sill(item)
            if _to_float(item.get("sill_height_mm")) is not None:
                _set_window_vertical_fields(item, level_elevations, item.get("sill_height_source", ""))
            else:
                _set_opening_vertical_binding_fields(
                    item,
                    level_elevations,
                    sill,
                    item.get("sill_height_source") or "unresolved",
                )
        return

    by_size: dict[tuple[int, int], list[dict]] = {}
    all_sills: list[float] = []
    for item in elevation_windows:
        width = _to_float(item.get("width_mm"))
        height = _to_float(item.get("height_mm"))
        sill = _to_float(item.get("sill_height_mm"))
        if sill is None:
            continue
        all_sills.append(sill)
        if width is not None and height is not None:
            by_size.setdefault((_rounded_size(width), _rounded_size(height)), []).append(item)

    default_sill = _most_common_number(all_sills)
    level_elevations = _level_elevation_lookup(model)
    for item in windows:
        if item.get("drawing_type") in {"elevation", "section"}:
            _set_window_vertical_fields(item, level_elevations, "elevation_view_source")
            continue
        if _to_float(item.get("sill_height_mm")) is not None:
            _set_window_vertical_fields(item, level_elevations, "input_sill_height")
            continue

        width = _to_float(item.get("width_mm"))
        height = _to_float(item.get("height_mm"))
        matched = []
        if width is not None and height is not None:
            matched = by_size.get((_rounded_size(width), _rounded_size(height)), [])
        if matched:
            sill = _most_common_number([_to_float(match.get("sill_height_mm")) for match in matched])
            source_ids = [match.get("id", "") for match in matched if match.get("id")]
            item["sill_height_mm"] = sill
            item["sill_height_source"] = "matched_from_elevation_window_size"
            item["vertical_match_evidence"] = ",".join(source_ids)
            item["notes"] = _append_note(item.get("notes", ""), f"sill_height_mm inferred from elevation windows with matching size: {sill:g}.")
        elif default_sill is not None:
            item["sill_height_mm"] = default_sill
            item["sill_height_source"] = "common_elevation_window_sill_height"
            item["vertical_match_evidence"] = "common sill height from elevation windows"
            item["notes"] = _append_note(item.get("notes", ""), f"sill_height_mm inferred from common elevation window sill height: {default_sill:g}.")
        else:
            _apply_default_plan_window_sill(item)

        if _to_float(item.get("sill_height_mm")) is not None:
            _set_window_vertical_fields(item, level_elevations, item.get("sill_height_source", ""))
        else:
            _set_opening_vertical_binding_fields(item, level_elevations, None, "unresolved")


def _apply_default_plan_window_sill(item: dict) -> bool:
    if item.get("drawing_type") in {"elevation", "section"}:
        return False
    if _to_float(item.get("sill_height_mm")) is not None:
        return False
    if not item.get("level") or _to_float(item.get("height_mm")) is None:
        return False

    item["sill_height_mm"] = DEFAULT_WINDOW_SILL_HEIGHT_MM
    item["sill_height_source"] = "default_plan_window_sill_height"
    item["sill_height_needs_review"] = True
    item["vertical_binding_needs_review"] = True
    item["notes"] = _append_note(
        item.get("notes", ""),
        "sill_height_mm defaulted to 900 for Revit placement; verify against elevation/section.",
    )
    if item.get("review_status") == "ready":
        item["review_status"] = "needs_review"
    return True


def _set_window_vertical_fields(item: dict, level_elevations: dict[str, float], source: str) -> None:
    sill = _to_float(item.get("sill_height_mm"))
    height = _to_float(item.get("height_mm"))
    if sill is None:
        return
    item.setdefault("sill_height_source", source or "input_sill_height")
    level_name = str(item.get("level", ""))
    base_elevation = level_elevations.get(level_name)
    source_text = " ".join(
        str(value or "").lower()
        for value in (source, item.get("sill_height_source"), item.get("matched_elevation_drawing"))
    )
    is_elevation_derived = "elevation" in source_text or "matched_elevation" in source_text
    if base_elevation is not None:
        item["level_elevation_mm"] = round(base_elevation, 3)
        story_height = _storey_height_at_elevation(base_elevation, level_elevations)
        # Elevation drawings record an absolute project height, while Revit's
        # Sill Height parameter is an offset from the hosting level. If an
        # elevation-derived value cannot physically fit within one storey,
        # rebase it to the closest lower project level before model creation.
        if (
            is_elevation_derived
            and height is not None
            and story_height is not None
            and sill + height > story_height + 1.0
        ):
            relative_sill = _relative_offset_from_project_elevation(sill, level_elevations)
            if relative_sill is not None and relative_sill + height <= story_height + 1.0:
                item["original_sill_height_mm"] = sill
                item["sill_height_mm"] = round(relative_sill, 3)
                item["sill_height_source"] = _append_note(
                    str(item.get("sill_height_source", "")),
                    "absolute_sill_elevation_rebased_from_elevation_view",
                )
                sill = relative_sill
        elif base_elevation > 0 and sill >= base_elevation - 1.0:
            item["original_sill_height_mm"] = sill
            item["sill_height_mm"] = round(sill - base_elevation, 3)
            item["sill_height_source"] = _append_note(
                str(item.get("sill_height_source", "")),
                "absolute_sill_elevation_converted_to_level_offset",
            )
            sill = _to_float(item.get("sill_height_mm"))
    if height is not None:
        item["head_height_mm"] = round(sill + height, 3)
    if base_elevation is not None:
        item["sill_elevation_mm"] = round(base_elevation + sill, 3)
        if height is not None:
            item["head_elevation_mm"] = round(base_elevation + sill + height, 3)
    _set_opening_vertical_binding_fields(item, level_elevations, sill, source)


def _storey_height_at_elevation(base_elevation: float, level_elevations: dict[str, float]) -> float | None:
    higher = sorted(
        elevation - base_elevation
        for elevation in level_elevations.values()
        if elevation > base_elevation + 1.0
    )
    return higher[0] if higher else None


def _relative_offset_from_project_elevation(value: float, level_elevations: dict[str, float]) -> float | None:
    candidate_bases = [elevation for elevation in level_elevations.values() if elevation <= value + 1.0]
    if not candidate_bases:
        return None
    return value - max(candidate_bases)


def _apply_door_level_information(model: dict) -> None:
    level_elevations = _level_elevation_lookup(model)
    for item in model.get("components", {}).get("doors", []):
        level_name = str(item.get("level", ""))
        base_elevation = level_elevations.get(level_name)
        item.setdefault("sill_height_mm", 0.0)
        item.setdefault("base_offset_mm", 0.0)
        item.setdefault("sill_height_source", "door_base_level")
        if base_elevation is not None:
            item["level_elevation_mm"] = round(base_elevation, 3)
            item["sill_elevation_mm"] = round(base_elevation, 3)
        _set_opening_vertical_binding_fields(item, level_elevations, 0.0, "door_base_level")


def apply_opening_vertical_bindings(model: dict) -> None:
    """Refresh Revit-ready vertical bindings after floor or height enrichment."""

    _apply_window_elevation_information(model)
    _apply_door_level_information(model)


def _set_opening_vertical_binding_fields(
    item: dict,
    level_elevations: dict[str, float],
    bottom_offset_mm: float | None,
    source: str,
) -> None:
    level_name = str(item.get("level", ""))
    height = _to_float(item.get("height_mm"))
    base_elevation = level_elevations.get(level_name)

    item["revit_base_level"] = level_name or None
    item["revit_base_level_elevation_mm"] = round(base_elevation, 3) if base_elevation is not None else None
    item["bottom_relative_elevation_mm"] = round(bottom_offset_mm, 3) if bottom_offset_mm is not None else None
    item["revit_sill_offset_mm"] = round(bottom_offset_mm, 3) if bottom_offset_mm is not None else None
    item["revit_height_mm"] = round(height, 3) if height is not None else None
    item["top_relative_elevation_mm"] = (
        round(bottom_offset_mm + height, 3)
        if bottom_offset_mm is not None and height is not None
        else None
    )
    item["revit_bottom_elevation_mm"] = (
        round(base_elevation + bottom_offset_mm, 3)
        if base_elevation is not None and bottom_offset_mm is not None
        else None
    )
    item["revit_top_elevation_mm"] = (
        round(base_elevation + bottom_offset_mm + height, 3)
        if base_elevation is not None and bottom_offset_mm is not None and height is not None
        else None
    )
    item["vertical_binding_source"] = source or "unresolved"
    ready = bool(level_name) and base_elevation is not None and bottom_offset_mm is not None and height is not None
    item["vertical_binding_status"] = "ready" if ready else "needs_review"
    if not ready:
        item["vertical_binding_reason"] = "Missing base level elevation, opening bottom offset, or opening height."
        if item.get("review_status") == "ready":
            item["review_status"] = "needs_review"
    else:
        item["vertical_binding_reason"] = "Base level, relative bottom elevation, and opening height are ready for Revit binding."


def _level_elevation_lookup(model: dict) -> dict[str, float]:
    lookup: dict[str, float] = {}
    for level in model.get("components", {}).get("levels", []):
        elevation = _to_float(level.get("elevation_mm"))
        if elevation is None:
            continue
        for key in (level.get("name", ""), level.get("id", "")):
            if key:
                lookup[str(key)] = elevation
    return lookup


def _rounded_size(value: float) -> int:
    return int(round(value / 10.0) * 10)


def _most_common_number(values) -> float | None:
    counts: dict[float, int] = {}
    for value in values:
        numeric = _to_float(value)
        if numeric is None:
            continue
        rounded = round(numeric, 3)
        counts[rounded] = counts.get(rounded, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _dedupe_grids(items: list[dict], report: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for item in items or []:
        key = _grid_dedupe_key(item)
        groups.setdefault(key, []).append(item)

    kept: list[dict] = []
    for key, group_items in groups.items():
        winner = sorted(group_items, key=_dedupe_preference, reverse=True)[0]
        winner["deduplication_status"] = "kept"
        winner["deduplication_key"] = key
        kept.append(winner)
        for duplicate in group_items:
            if duplicate is winner:
                continue
            report.append(
                _dedupe_report_row(
                    group="grids",
                    item=duplicate,
                    duplicate_of=winner.get("id", ""),
                    reason="Same grid name appears in multiple drawings; kept the floor-plan grid for Revit modeling.",
                )
            )
    return sorted(kept, key=lambda item: str(item.get("id", "")))


def _remove_non_plan_projection_components(group: str, items: list[dict], report: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for item in items or []:
        drawing_type = str(item.get("drawing_type", ""))
        if drawing_type in NON_SPATIAL_VIEW_TYPES:
            if group == "stairs" and _is_stair_plan_detail_candidate(item):
                item["deduplication_status"] = "retained_for_spatial_stair_matching"
                item["coordinate_authority"] = "no_project_xy"
                item["spatial_matching_required"] = True
                kept.append(item)
                continue
            reason = (
                "Detail-view geometry is local explanatory geometry; its coordinates are forbidden for project placement. "
                "Only matched dimensions, construction and material evidence may enrich a floor-plan component."
                if drawing_type == "detail"
                else "Element came from an elevation/section projection and is excluded from plan-based Revit modeling input."
            )
            report.append(
                _dedupe_report_row(
                    group=group,
                    item=item,
                    duplicate_of="",
                    reason=reason,
                )
            )
            continue
        item["deduplication_status"] = "kept"
        kept.append(item)
    return kept


def _is_stair_plan_detail_candidate(item: dict) -> bool:
    if str(item.get("drawing_type", "")).strip().lower() != "detail":
        return False
    return _is_stair_plan_drawing_name(item.get("drawing_name", ""))


def _is_stair_plan_drawing_name(value) -> bool:
    name = str(value or "").strip().lower()
    return "楼梯平面" in name or "stair plan" in name or "stair_plan" in name


def _apply_cross_view_detail_fusion(model: dict, drawing_info: dict[str, dict], report: list[dict]) -> None:
    """Attach detail parameters to plan components without importing detail coordinates."""
    evidence_rows: list[dict] = []
    links: list[dict] = []
    components = model.get("components", {})
    for group, allowed_fields in DETAIL_ENRICHMENT_FIELDS.items():
        items = components.get(group, [])
        if not isinstance(items, list):
            continue
        plan_items = [item for item in items if item.get("drawing_type") == "floor_plan"]
        detail_items = [item for item in items if item.get("drawing_type") == "detail"]
        for detail in detail_items:
            match, match_basis, confidence = _match_detail_to_plan_component(group, detail, plan_items)
            copied_fields: list[str] = []
            status = "unmatched_no_spatial_anchor"
            if match is not None:
                copied_fields = _merge_detail_fields(match, detail, allowed_fields, confidence)
                match.setdefault("detail_source_ids", []).append(detail.get("id", ""))
                match.setdefault("cross_view_evidence", []).append(
                    {
                        "source_drawing_id": detail.get("drawing_id", ""),
                        "source_component_id": detail.get("id", ""),
                        "source_view_type": "detail",
                        "match_basis": match_basis,
                        "match_confidence": confidence,
                        "copied_fields": copied_fields,
                        "coordinate_fields_used": [],
                    }
                )
                status = "matched_to_plan_component"
            elif plan_items:
                status = "ambiguous_plan_match"

            evidence = {
                "component_group": group,
                "detail_component_id": detail.get("id", ""),
                "detail_drawing_id": detail.get("drawing_id", ""),
                "detail_drawing_name": detail.get("drawing_name", ""),
                "detail_subject": detail.get("detail_subject", ""),
                "status": status,
                "matched_plan_component_id": match.get("id", "") if match else "",
                "match_basis": match_basis,
                "match_confidence": confidence,
                "copied_fields": copied_fields,
                "coordinate_usage": "forbidden",
                "coordinate_reason": "Detail coordinates belong to the detail viewport and do not represent project XY/Z placement.",
                "available_parameters": _detail_parameter_snapshot(detail, allowed_fields),
            }
            evidence_rows.append(evidence)
            links.append(
                {
                    "relationship_type": "detail_evidence_to_plan_component",
                    "component_group": group,
                    "detail_component_id": detail.get("id", ""),
                    "plan_component_id": match.get("id", "") if match else "",
                    "status": status,
                    "coordinate_authority": "none",
                    "parameter_authority": "matched_detail_fields_only",
                }
            )

    model["view_semantic_handoff"] = {
        "schema_version": "1.0",
        "policy": {
            "floor_plan": "authoritative for project XY, host and floor placement",
            "elevation_section": "vertical dimensions and level relationships only; no project XY",
            "detail": "component dimensions, construction, material and type evidence only; no project coordinates",
            "unmatched_detail": "preserve as evidence and block spatial execution until a plan component is matched",
        },
        "drawings": [
            {
                "drawing_id": drawing_id,
                **info,
            }
            for drawing_id, info in sorted(drawing_info.items())
        ],
        "detail_evidence": evidence_rows,
        "cross_view_links": links,
    }


def _match_detail_to_plan_component(group: str, detail: dict, plan_items: list[dict]) -> tuple[dict | None, str, float]:
    if not plan_items:
        return None, "no_floor_plan_candidate", 0.0

    detail_mark = str(detail.get("opening_mark", "") or "").strip().upper()
    if detail_mark:
        exact = [item for item in plan_items if str(item.get("opening_mark", "") or "").strip().upper() == detail_mark]
        if len(exact) == 1:
            return exact[0], "exact_component_mark", 0.98

    detail_type = _stable_detail_type(detail)
    if detail_type:
        same_type = [item for item in plan_items if _stable_detail_type(item) == detail_type]
        if len(same_type) == 1:
            return same_type[0], "unique_component_type", 0.88

    same_size = [item for item in plan_items if _same_detail_size(detail, item)]
    if len(same_size) == 1:
        return same_size[0], "unique_matching_size", 0.82
    if len(plan_items) == 1:
        return plan_items[0], "single_plan_candidate", 0.7
    return None, "multiple_floor_plan_candidates_without_unique_mark_or_type", 0.0


def _merge_detail_fields(target: dict, detail: dict, allowed_fields: tuple[str, ...], confidence: float) -> list[str]:
    copied: list[str] = []
    for field in allowed_fields:
        value = detail.get(field)
        if value in (None, "", [], {}):
            continue
        current = target.get(field)
        can_replace = confidence >= 0.88 or current in (None, "", [], {}) or str(target.get(f"{field}_source", "")).startswith(("default", "unresolved"))
        if not can_replace:
            continue
        target[field] = value
        source_value = f"matched_detail:{detail.get('drawing_id', '')}:{detail.get('id', '')}"
        target.setdefault("detail_parameter_sources", {})[field] = source_value
        source_field = _detail_source_field(field)
        if source_field:
            target[source_field] = source_value
        copied.append(field)
    if copied:
        target["notes"] = _append_note(
            target.get("notes", ""),
            "Detail evidence supplied parameters only; project position remains from the floor plan.",
        )
    return copied


def _detail_parameter_snapshot(item: dict, allowed_fields: tuple[str, ...]) -> dict:
    return {field: item.get(field) for field in allowed_fields if item.get(field) not in (None, "", [], {})}


def _stable_detail_type(item: dict) -> str:
    value = str(item.get("semantic_type") or item.get("design_type") or item.get("type") or "").strip().lower()
    return "" if value in {"", "unresolved", "generic door", "generic window", "stair", "railing"} else value


def _same_detail_size(left: dict, right: dict) -> bool:
    compared = 0
    for field in ("width_mm", "height_mm", "thickness_mm"):
        a, b = _to_float(left.get(field)), _to_float(right.get(field))
        if a is None or b is None:
            continue
        compared += 1
        if abs(a - b) > 10.0:
            return False
    return compared >= 2


def _view_semantic_role(drawing_type: str) -> str:
    return {
        "floor_plan": "spatial_primary",
        "elevation": "vertical_reference",
        "section": "vertical_reference",
        "detail": "component_detail_evidence",
    }.get(str(drawing_type), "unresolved_view")


def _coordinate_authority(drawing_type: str) -> str:
    return "project_xy_authority" if drawing_type == "floor_plan" else "no_project_xy" if drawing_type in NON_SPATIAL_VIEW_TYPES else "unverified"


def _detail_source_field(field: str) -> str:
    return {
        "height_mm": "height_source",
        "width_mm": "width_source",
        "thickness_mm": "thickness_source",
        "sill_height_mm": "sill_height_source",
        "material": "material_source",
    }.get(field, "")


def _detail_subject_from_name(name: str, drawing_type: str) -> str:
    if drawing_type != "detail":
        return ""
    text = str(name or "").lower()
    for subject, pattern in (
        ("stair", r"楼梯|stair"),
        ("door_window", r"门窗|door.*window|window.*door"),
        ("door", r"门|door"),
        ("window", r"窗|window"),
        ("wall", r"墙|wall"),
        ("roof", r"屋面|屋顶|roof"),
        ("railing", r"栏杆|扶手|railing|handrail"),
    ):
        if re.search(pattern, text, re.I):
            return subject
    return ""


def _grid_dedupe_key(item: dict) -> str:
    name = str(item.get("name", "")).strip().upper()
    return f"grid_name:{name}" if name else f"grid_geometry:{_line_signature(item)}"


def _line_signature(item: dict) -> str:
    start = item.get("start") or {}
    end = item.get("end") or {}
    values = [
        round(float(start.get("x", 0)) / 10) * 10,
        round(float(start.get("y", 0)) / 10) * 10,
        round(float(end.get("x", 0)) / 10) * 10,
        round(float(end.get("y", 0)) / 10) * 10,
    ]
    return ",".join(str(int(value)) for value in values)


def _dedupe_preference(item: dict) -> tuple:
    drawing_type = str(item.get("drawing_type", ""))
    source_score = 3 if drawing_type == "floor_plan" else 2 if drawing_type == "" else 1
    review_score = 1 if str(item.get("review_status", "")) in {"ready", "confirmed"} else 0
    confidence = float(item.get("confidence") or 0)
    return (source_score, review_score, confidence)


def _dedupe_report_row(group: str, item: dict, duplicate_of: str, reason: str) -> dict:
    return {
        "component_group": group,
        "component_id": item.get("id", ""),
        "component_name": item.get("name", ""),
        "duplicate_of": duplicate_of,
        "drawing_id": item.get("drawing_id", ""),
        "drawing_name": item.get("drawing_name", ""),
        "drawing_type": item.get("drawing_type", ""),
        "action": "removed_from_modeling_input",
        "reason": reason,
    }


def _component_counts(model: dict) -> dict[str, int]:
    return {
        group: len(items)
        for group, items in model.get("components", {}).items()
        if isinstance(items, list)
    }


def _apply_wall_spatial_roles(model: dict) -> None:
    walls = model.get("components", {}).get("walls", [])
    if not isinstance(walls, list):
        return

    grouped: dict[str, list[dict]] = {}
    for wall in walls:
        drawing_type = str(wall.get("drawing_type", ""))
        if drawing_type in NON_SPATIAL_VIEW_TYPES:
            _set_wall_scope(
                wall,
                "unknown",
                "projection_not_plan",
                "Wall came from a detail/elevation/section view, not a floor-plan spatial boundary.",
                0.2,
                True,
                "unknown",
            )
            continue
        key = str(wall.get("drawing_id") or wall.get("base_level") or "unassigned")
        grouped.setdefault(key, []).append(wall)

    for items in grouped.values():
        if len(items) < 4:
            for wall in items:
                _set_wall_scope(
                    wall,
                    "unknown",
                    "insufficient_plan_context",
                    "Fewer than four walls were available in this plan group, so an outer envelope could not be trusted.",
                    0.35,
                    True,
                    "unknown",
                )
            continue

        bounds = _wall_group_bounds(items)
        if bounds is None:
            continue
        thicknesses = [_to_float(item.get("thickness_mm")) for item in items]
        numeric_thicknesses = [value for value in thicknesses if value is not None]
        median_thickness = _median(numeric_thicknesses)
        tolerance = max(250.0, (median_thickness or 200.0) * 1.35)

        for wall in items:
            if isinstance(wall.get("is_exterior"), bool):
                scope = "exterior" if wall.get("is_exterior") else "interior"
                _set_wall_scope(
                    wall,
                    scope,
                    "existing_csv",
                    f"Input Is_Exterior={wall.get('is_exterior')}.",
                    max(_to_float(wall.get("confidence")) or 0.0, 0.8),
                    False,
                    wall.get("wall_outer_edge", "unknown") or "unknown",
                )
                continue

            scope, edge, confidence, needs_review, reason = _classify_wall_by_plan_envelope(
                wall,
                bounds,
                tolerance,
                median_thickness,
            )
            _set_wall_scope(
                wall,
                scope,
                "plan_outer_envelope",
                _wall_scope_evidence(wall, bounds, tolerance, edge, median_thickness),
                confidence,
                needs_review,
                edge,
                reason,
            )


def _wall_group_bounds(items: list[dict]) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for wall in items:
        for key in ("start", "end"):
            point = wall.get(key)
            if not isinstance(point, dict):
                continue
            x = _to_float(point.get("x"))
            y = _to_float(point.get("y"))
            if x is not None and y is not None:
                xs.append(x)
                ys.append(y)
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _classify_wall_by_plan_envelope(
    wall: dict,
    bounds: tuple[float, float, float, float],
    tolerance: float,
    median_thickness: float | None,
) -> tuple[str, str, float, bool, str]:
    min_x, min_y, max_x, max_y = bounds
    start = wall.get("start") or {}
    end = wall.get("end") or {}
    x1 = _to_float(start.get("x"))
    y1 = _to_float(start.get("y"))
    x2 = _to_float(end.get("x"))
    y2 = _to_float(end.get("y"))
    if None in (x1, y1, x2, y2):
        return "unknown", "unknown", 0.2, True, "Missing wall start/end coordinates."

    dx = abs(x2 - x1)
    dy = abs(y2 - y1)
    mid_x = (x1 + x2) / 2
    mid_y = (y1 + y2) / 2
    edge = "unknown"
    on_outer = False
    reason = "Wall centerline is inside the plan outer envelope."

    if dx >= dy * 3:
        if abs(mid_y - min_y) <= tolerance:
            edge = "south"
            on_outer = True
        elif abs(mid_y - max_y) <= tolerance:
            edge = "north"
            on_outer = True
    elif dy >= dx * 3:
        if abs(mid_x - min_x) <= tolerance:
            edge = "west"
            on_outer = True
        elif abs(mid_x - max_x) <= tolerance:
            edge = "east"
            on_outer = True
    else:
        distances = {
            "west": abs(mid_x - min_x),
            "east": abs(mid_x - max_x),
            "south": abs(mid_y - min_y),
            "north": abs(mid_y - max_y),
        }
        edge, distance = min(distances.items(), key=lambda item: item[1])
        on_outer = distance <= tolerance
        reason = "Non-orthogonal wall classified by midpoint distance to outer envelope."

    thickness = _to_float(wall.get("thickness_mm"))
    thickness_delta = 0.0 if thickness is None or median_thickness is None else thickness - median_thickness
    thickness_note = "no thickness evidence"
    if thickness is not None and median_thickness is not None:
        thickness_note = f"thickness {thickness:g} vs plan median {median_thickness:g}"

    if on_outer:
        confidence = 0.84
        if thickness_delta >= 40:
            confidence += 0.06
        elif thickness_delta <= -80:
            confidence -= 0.12
            reason = f"Outer-envelope position says exterior, but wall is thin relative to the plan median ({thickness_note})."
        else:
            reason = f"Wall lies on the {edge} outer envelope of its floor-plan wall group ({thickness_note})."
        return "exterior", edge, round(max(0.0, min(confidence, 0.95)), 3), confidence < 0.78, reason

    confidence = 0.78
    if thickness_delta <= -40:
        confidence += 0.05
    elif thickness_delta >= 120:
        axis_length = (dx * dx + dy * dy) ** 0.5
        recognition_type = str(wall.get("type") or "").lower()
        is_short_junction = (
            recognition_type in {"short_paired_wall_lines", "t_pier_wall_lines"}
            and thickness is not None
            and axis_length <= max(600.0, thickness * 1.5)
        )
        if is_short_junction:
            confidence += 0.04
            reason = (
                "Short interior wall pier/junction retained as interior from plan position; "
                f"its local thickness is not exterior-wall evidence ({thickness_note}; length {axis_length:g})."
            )
        else:
            confidence -= 0.08
            reason = f"Interior position says interior, but wall is thick relative to the plan median ({thickness_note})."
    return "interior", "inside", round(max(0.0, min(confidence, 0.9)), 3), confidence < 0.72, reason


def _set_wall_scope(
    wall: dict,
    scope: str,
    source: str,
    evidence: str,
    confidence: float,
    needs_review: bool,
    outer_edge: str,
    reason: str = "",
) -> None:
    wall["wall_scope"] = scope
    wall["is_exterior"] = True if scope == "exterior" else False if scope == "interior" else None
    wall["wall_scope_source"] = source
    wall["wall_scope_evidence"] = evidence
    wall["wall_scope_confidence"] = round(max(0.0, min(float(confidence or 0.0), 1.0)), 3)
    wall["wall_scope_needs_review"] = bool(needs_review)
    wall["wall_outer_edge"] = outer_edge
    wall["wall_scope_reason"] = reason
    if needs_review:
        wall["notes"] = _append_note(wall.get("notes", ""), f"Wall scope needs review: {reason or evidence}")


def _wall_scope_evidence(
    wall: dict,
    bounds: tuple[float, float, float, float],
    tolerance: float,
    edge: str,
    median_thickness: float | None,
) -> str:
    start = wall.get("start") or {}
    end = wall.get("end") or {}
    thickness = _to_float(wall.get("thickness_mm"))
    return (
        f"edge={edge}; start=({start.get('x')},{start.get('y')}); end=({end.get('x')},{end.get('y')}); "
        f"plan_bounds=({bounds[0]:g},{bounds[1]:g},{bounds[2]:g},{bounds[3]:g}); "
        f"tolerance={tolerance:g}; thickness={'' if thickness is None else f'{thickness:g}'}; "
        f"median_thickness={'' if median_thickness is None else f'{median_thickness:g}'}"
    )


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _wall_height_is_default(wall: dict) -> bool:
    height = _to_float(wall.get("height_mm"))
    notes = str(wall.get("notes", "")).lower()
    if height is None:
        return True
    return abs(height - DEFAULT_LEVEL_HEIGHT_MM) <= 1.0 and ("default" in notes or "默认" in notes or "未从图纸直接识别" in notes)


def _append_note(existing: str, note: str) -> str:
    if not existing:
        return note
    return existing + " | " + note


def _infer_levels_from_standard_package(source_path: Path, existing_levels: list[dict]) -> dict:
    package = _standard_package_root(source_path)
    if package is None:
        return _empty_floor_analysis("No standardized model package was found.")

    csv_root = _package_csv_root(package)
    dimensions = _read_csv_rows(csv_root / "Dimensions.csv")
    drawings = _read_csv_rows(csv_root / "Drawings.csv")
    marks = _elevation_marks_from_dimensions(dimensions, drawings)
    elevations = _architectural_level_elevations(
        _unique_sorted(mark["elevation_mm"] for mark in marks)
    )
    replace_existing_levels = (
        _should_replace_existing_levels(existing_levels)
        or _levels_incomplete_for_floor_plans(existing_levels, drawings)
    )
    if len(existing_levels) >= 2 and not replace_existing_levels:
        return _floor_analysis_from_existing_levels(existing_levels, drawings, marks)

    if len(elevations) < 2:
        return _empty_floor_analysis("Fewer than two elevation marks were found in elevation drawings.")

    inferred_levels = [
        {
            "id": f"LEVEL-{index:03d}",
            "type": "level",
            "name": _level_name(index, len(elevations)),
            "elevation_mm": elevation,
            "source": "elevation_marks:standardized_model_package",
            "confidence": _inferred_level_confidence(marks, elevation),
            "review_status": "needs_review",
            "notes": "Inferred from elevation drawing marks before material completion; confirm level naming before Revit modeling.",
        }
        for index, elevation in enumerate(elevations, start=1)
    ]

    floors = []
    for index in range(len(inferred_levels)):
        base = inferred_levels[index]
        top = inferred_levels[index + 1] if index + 1 < len(inferred_levels) else None
        floors.append(
            {
                "floor_number": index + 1,
                "floor_name": f"{index + 1}F",
                "base_level_id": base["id"],
                "base_level_name": base["name"],
                "base_elevation_mm": base["elevation_mm"],
                "top_level_id": top["id"] if top else "",
                "top_level_name": top["name"] if top else "",
                "top_elevation_mm": top["elevation_mm"] if top else "",
                "floor_height_mm": round(top["elevation_mm"] - base["elevation_mm"], 3) if top else None,
                "source": "elevation_marks" if top else "roof_level_reference",
                "review_status": "needs_review",
            }
        )

    return {
        "method": "elevation_marks_first",
        "status": "inferred_from_elevation_drawings",
        "reason": "Elevation marks were detected before floor-plan component binding.",
        "elevation_marks": marks,
        "levels": inferred_levels,
        "floors": floors,
        "drawing_floor_assignments": _assign_floor_plans(drawings, floors),
        "replaced_input_levels": replace_existing_levels,
    }


def _floor_analysis_from_existing_levels(levels: list[dict], drawings: list[dict[str, str]], marks: list[dict]) -> dict:
    sorted_levels = sorted(levels, key=lambda item: float(item.get("elevation_mm") or 0.0))
    floors = []
    for index, base in enumerate(sorted_levels):
        top = sorted_levels[index + 1] if index + 1 < len(sorted_levels) else None
        floor_height = _to_float(base.get("floor_height_mm"))
        if floor_height is None and top is not None:
            floor_height = round(float(top.get("elevation_mm") or 0.0) - float(base.get("elevation_mm") or 0.0), 3)
        floors.append(
            {
                "floor_number": index + 1,
                "floor_name": base.get("name", f"{index + 1}F"),
                "base_level_id": base.get("id", ""),
                "base_level_name": base.get("name", ""),
                "base_elevation_mm": base.get("elevation_mm", ""),
                "top_level_id": top.get("id", "") if top else "",
                "top_level_name": top.get("name", "") if top else "",
                "top_elevation_mm": top.get("elevation_mm", "") if top else "",
                "floor_height_mm": floor_height,
                "source": "levels_csv",
                "review_status": base.get("review_status", "needs_review"),
            }
        )
    return {
        "method": "explicit_levels_first",
        "status": "from_input_levels",
        "reason": "Levels.csv provided usable level names and elevations; elevation marks are kept as supporting evidence.",
        "elevation_marks": marks,
        "levels": sorted_levels,
        "floors": floors,
        "drawing_floor_assignments": _assign_floor_plans(drawings, floors),
        "replaced_input_levels": False,
    }


def _standard_package_root(path: Path) -> Path | None:
    if path.is_dir() and ((path / "csv_tables").is_dir() or _is_flat_standard_csv_root(path)):
        return path
    nested = path / "02_标准化模型数据"
    if nested.is_dir() and ((nested / "csv_tables").is_dir() or _is_flat_standard_csv_root(nested)):
        return nested
    if path.is_dir():
        for candidate in path.glob("*"):
            if candidate.is_dir() and ((candidate / "csv_tables").is_dir() or _is_flat_standard_csv_root(candidate)):
                return candidate
    if path.name.lower() == "ai_model.json" and ((path.parent / "csv_tables").is_dir() or _is_flat_standard_csv_root(path.parent)):
        return path.parent
    return None


def _is_flat_standard_csv_root(path: Path) -> bool:
    return (path / "Manifest.csv").is_file() and (path / "Drawings.csv").is_file()


def _package_csv_root(package: Path) -> Path:
    csv_tables = package / "csv_tables"
    return csv_tables if csv_tables.is_dir() else package


def _empty_floor_analysis(reason: str) -> dict:
    return {
        "method": "elevation_marks_first",
        "status": "not_inferred",
        "reason": reason,
        "elevation_marks": [],
        "levels": [],
        "floors": [],
        "drawing_floor_assignments": [],
    }


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8", errors="ignore")
    return [
        {_normalize_key(k): (v or "").strip() for k, v in row.items()}
        for row in csv.DictReader(text.splitlines())
    ]


def _normalize_key(value: str) -> str:
    return str(value or "").lstrip("\ufeff").strip().lower().replace(" ", "_").replace("-", "_")


def _elevation_marks_from_dimensions(dimensions: list[dict[str, str]], drawings: list[dict[str, str]]) -> list[dict]:
    drawing_types = {row.get("drawing_id", ""): row.get("drawing_type", "") for row in drawings}
    drawing_names = {row.get("drawing_id", ""): row.get("drawing_name", "") for row in drawings}
    marks = []
    for row in dimensions:
        if row.get("dimension_type") != "elevation_mark":
            continue
        drawing_id = row.get("drawing_id", "")
        if drawing_types.get(drawing_id) not in {"elevation", "section"}:
            continue
        value = _to_float(row.get("value"))
        if value is None:
            continue
        marks.append(
            {
                "dimension_id": row.get("dimension_id", ""),
                "drawing_id": drawing_id,
                "drawing_name": drawing_names.get(drawing_id, ""),
                "elevation_mm": value,
                "confidence": _to_float(row.get("confidence")) or 0.5,
            }
        )
    return marks


def _unique_sorted(values) -> list[float]:
    unique: list[float] = []
    for value in sorted(float(v) for v in values):
        if not unique or abs(value - unique[-1]) > 1.0:
            unique.append(round(value, 3))
    return unique


def _dedupe_same_position_walls(items: list[dict], report: list[dict]) -> tuple[list[dict], dict[str, str]]:
    ordered = sorted(items, key=_wall_recheck_preference, reverse=True)
    kept: list[dict] = []
    replacements: dict[str, str] = {}
    for item in ordered:
        duplicate_of = next((wall for wall in kept if _walls_share_modeling_position(wall, item)), None)
        if duplicate_of is None:
            item["wall_recheck_status"] = "unique"
            kept.append(item)
            continue

        item_id = str(item.get("id") or "")
        winner_id = str(duplicate_of.get("id") or "")
        if item_id and winner_id:
            replacements[item_id] = winner_id
        duplicate_of.setdefault("duplicate_source_ids", []).append(item_id)
        duplicate_of["wall_recheck_status"] = "kept_after_deduplication"
        conflicts = _wall_property_conflicts(duplicate_of, item)
        report.append(
            _dedupe_report_row(
                "walls",
                item,
                winner_id,
                "same-position wall removed after final wall recheck"
                + (f"; retained wall properties require review: {', '.join(conflicts)}" if conflicts else ""),
            )
        )
    _block_ambiguous_cross_drawing_wall_overlaps(kept, report)
    kept.sort(key=lambda wall: str(wall.get("id") or ""))
    return kept, replacements


def _wall_recheck_preference(item: dict) -> tuple:
    start = item.get("start") or {}
    end = item.get("end") or {}
    x1 = _to_float(start.get("x")) or 0.0
    y1 = _to_float(start.get("y")) or 0.0
    x2 = _to_float(end.get("x")) or 0.0
    y2 = _to_float(end.get("y")) or 0.0
    length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    property_score = sum(bool(item.get(field)) for field in ("thickness_mm", "material", "height_mm"))
    return (length, property_score, *_dedupe_preference(item))


def _walls_share_modeling_position(a: dict, b: dict, require_floor_scope: bool = True) -> bool:
    level_a = str(a.get("level") or a.get("base_level") or "").strip().lower()
    level_b = str(b.get("level") or b.get("base_level") or "").strip().lower()
    if level_a and level_b and level_a != level_b:
        return False
    points = []
    for wall in (a, b):
        start = wall.get("start") or {}
        end = wall.get("end") or {}
        values = tuple(_to_float(point.get(axis)) for point in (start, end) for axis in ("x", "y"))
        if any(value is None for value in values):
            return False
        points.append(values)
    ax1, ay1, ax2, ay2 = points[0]
    bx1, by1, bx2, by2 = points[1]
    adx, ady = ax2 - ax1, ay2 - ay1
    bdx, bdy = bx2 - bx1, by2 - by1
    alen = (adx * adx + ady * ady) ** 0.5
    blen = (bdx * bdx + bdy * bdy) ** 0.5
    if alen <= 1.0 or blen <= 1.0:
        return False
    direction_alignment = abs((adx * bdx + ady * bdy) / (alen * blen))
    if direction_alignment < 0.99985:
        return False
    line_distance_1 = abs((bx1 - ax1) * ady - (by1 - ay1) * adx) / alen
    line_distance_2 = abs((bx2 - ax1) * ady - (by2 - ay1) * adx) / alen
    if max(line_distance_1, line_distance_2) > 20.0:
        return False
    ux, uy = adx / alen, ady / alen
    b0 = (bx1 - ax1) * ux + (by1 - ay1) * uy
    b1 = (bx2 - ax1) * ux + (by2 - ay1) * uy
    overlap = max(0.0, min(alen, max(b0, b1)) - max(0.0, min(b0, b1)))
    if overlap < min(alen, blen) * 0.98 - 20.0:
        return False
    if not require_floor_scope:
        return True
    drawing_a = str(a.get("drawing_id") or "").strip()
    drawing_b = str(b.get("drawing_id") or "").strip()
    if not drawing_a or not drawing_b or drawing_a == drawing_b:
        return True
    floor_a = str(a.get("floor_number") or "").strip()
    floor_b = str(b.get("floor_number") or "").strip()
    status_a = str(a.get("floor_assignment_review_status") or "").strip().lower()
    status_b = str(b.get("floor_assignment_review_status") or "").strip().lower()
    return bool(floor_a and floor_b and floor_a == floor_b and "review" not in status_a and "review" not in status_b)


def _block_ambiguous_cross_drawing_wall_overlaps(items: list[dict], report: list[dict]) -> None:
    reported: set[str] = set()
    for index, first in enumerate(items):
        for second in items[index + 1 :]:
            drawing_a = str(first.get("drawing_id") or "").strip()
            drawing_b = str(second.get("drawing_id") or "").strip()
            if not drawing_a or not drawing_b or drawing_a == drawing_b:
                continue
            if not _walls_share_modeling_position(first, second, require_floor_scope=False):
                continue
            if _walls_share_modeling_position(first, second):
                continue
            for item, other in ((first, second), (second, first)):
                item_id = str(item.get("id") or "")
                if item_id in reported:
                    continue
                reported.add(item_id)
                item["modeling_status"] = "blocked"
                item["revit_execution_scope"] = "review_only"
                item["review_status"] = "needs_review"
                item["needs_review"] = True
                item["wall_recheck_status"] = "blocked_ambiguous_cross_drawing_overlap"
                reason = (
                    "Wall overlaps a wall from another drawing at the same assigned level, but floor identity is not "
                    f"reliable ({item_id} vs {other.get('id', '')}). Resolve floor binding before modeling."
                )
                item["modeling_reason"] = reason
                report.append(
                    {
                        "component_group": "walls",
                        "component_id": item_id,
                        "component_name": item.get("name", ""),
                        "duplicate_of": "",
                        "drawing_id": item.get("drawing_id", ""),
                        "drawing_name": item.get("drawing_name", ""),
                        "drawing_type": item.get("drawing_type", ""),
                        "action": "blocked_from_modeling_input",
                        "reason": reason,
                    }
                )


def _wall_property_conflicts(a: dict, b: dict) -> list[str]:
    conflicts = []
    thickness_a = _to_float(a.get("thickness_mm"))
    thickness_b = _to_float(b.get("thickness_mm"))
    if thickness_a is not None and thickness_b is not None and abs(thickness_a - thickness_b) > 5.0:
        conflicts.append(f"thickness {thickness_a:g}/{thickness_b:g} mm")
    material_a = str(a.get("material") or "").strip().lower()
    material_b = str(b.get("material") or "").strip().lower()
    if material_a and material_b and material_a != material_b:
        conflicts.append("material")
    return conflicts


def _remap_wall_references(model: dict, replacements: dict[str, str]) -> None:
    if not replacements:
        return
    for items in model.get("components", {}).values():
        if not isinstance(items, list):
            continue
        for item in items:
            host = str(item.get("host_wall_id") or "")
            if host in replacements:
                item["host_wall_id"] = replacements[host]
            for field in ("adjacent_wall_ids", "wall_ids"):
                values = item.get(field)
                if not isinstance(values, list):
                    continue
                remapped = [replacements.get(str(value), str(value)) for value in values]
                item[field] = list(dict.fromkeys(remapped))


def _architectural_level_elevations(elevations: list[float]) -> list[float]:
    """Exclude a terminal parapet/top marker from the Revit storey sequence."""

    values = list(elevations)
    if len(values) < 4:
        return values
    storey_gaps = [values[index + 1] - values[index] for index in range(len(values) - 2)]
    typical_gap = _median([gap for gap in storey_gaps if gap >= 1800.0])
    terminal_gap = values[-1] - values[-2]
    if typical_gap and 0 < terminal_gap <= 1500.0 and terminal_gap < typical_gap * 0.5:
        return values[:-1]
    return values


def _inferred_level_confidence(marks: list[dict], elevation: float) -> float:
    matching = [mark for mark in marks if abs(mark["elevation_mm"] - elevation) <= 1.0]
    if not matching:
        return 0.5
    avg = sum(float(mark.get("confidence") or 0.5) for mark in matching) / len(matching)
    repeat_bonus = min(0.2, 0.05 * (len(matching) - 1))
    return round(max(0.0, min(1.0, avg + repeat_bonus)), 3)


def _level_name(index: int, total: int) -> str:
    if index == total:
        return "Roof Level"
    return f"Level {index}"


def _assign_floor_plans(drawings: list[dict[str, str]], floors: list[dict]) -> list[dict]:
    floor_plans = sorted(
        (row for row in drawings if row.get("drawing_type") == "floor_plan"),
        key=_floor_plan_sort_key,
    )
    assignments = []
    for index, row in enumerate(floor_plans):
        floor = _match_floor_by_level_id(row.get("level_id", ""), floors)
        matched_by_level_id = floor is not None
        reason = "Matched from explicit Drawings.csv Level_ID."
        if floor is None:
            floor = _match_floor_by_name(row.get("drawing_name", ""), floors)
            reason = "Matched from drawing name."
        drawing_number = _to_float(row.get("drawing_number"))
        if floor is None and drawing_number is not None and drawing_number.is_integer():
            candidate_number = int(drawing_number)
            if 0 < candidate_number <= len(floors):
                floor = floors[candidate_number - 1]
                reason = "Matched from floor-plan drawing number."
        if floor is None and len(floor_plans) == len(floors) and index < len(floors):
            floor = floors[index]
            reason = "Matched by ordered floor-plan sequence because drawing names contain no floor token."
        if floor is None and len(floor_plans) == 1 and floors:
            floor = floors[0]
            reason = "Single floor plan assigned to first inferred floor."
        assignments.append(
            {
                "drawing_id": row.get("drawing_id", ""),
                "drawing_name": row.get("drawing_name", ""),
                "drawing_type": row.get("drawing_type", ""),
                "assigned_floor_number": floor.get("floor_number", "") if floor else "",
                "assigned_floor_name": floor.get("floor_name", "") if floor else "",
                "assigned_base_level": floor.get("base_level_name", "") if floor else "",
                "assigned_top_level": floor.get("top_level_name", "") if floor else "",
                "floor_height_mm": floor.get("floor_height_mm", "") if floor else "",
                "confidence": 0.98 if matched_by_level_id else (0.65 if floor else 0.0),
                "review_status": "ready" if matched_by_level_id else "needs_review",
                "reason": reason if floor else "Could not assign floor plan.",
            }
        )
    return assignments


def _match_floor_by_level_id(level_id: str, floors: list[dict]) -> dict | None:
    expected = str(level_id or "").strip().lower()
    if not expected:
        return None
    for floor in floors:
        candidate = str(floor.get("base_level_id") or "").strip().lower()
        if candidate and candidate == expected:
            return floor
    return None


def _floor_plan_sort_key(row: dict[str, str]) -> tuple[int, float, str]:
    drawing_number = _to_float(row.get("drawing_number"))
    return (0 if drawing_number is not None else 1, drawing_number or 0.0, row.get("drawing_id", ""))


def _match_floor_by_name(name: str, floors: list[dict]) -> dict | None:
    text = re.sub(r"\s+", "", str(name or "").lower())
    if not text:
        return None

    # Match the actual level name first. A project may start at Level 2, so a
    # drawing token must never be used as a zero-based/list position.
    for floor in floors:
        base_level_name = re.sub(r"\s+", "", str(floor.get("base_level_name") or "").lower())
        floor_name = re.sub(r"\s+", "", str(floor.get("floor_name") or "").lower())
        if base_level_name and base_level_name in text:
            return floor
        if floor_name and floor_name in text:
            return floor

    if any(token in text for token in ("屋顶", "屋面", "roof")):
        for floor in floors:
            level_text = " ".join(
                str(floor.get(key) or "").lower()
                for key in ("base_level_name", "floor_name")
            )
            if any(token in level_text for token in ("屋顶", "屋面", "roof")):
                return floor

    drawing_floor_number = _explicit_floor_number(name)
    if drawing_floor_number is not None:
        for floor in floors:
            level_number = _explicit_floor_number(
                f"{floor.get('base_level_name', '')} {floor.get('floor_name', '')}"
            )
            if level_number == drawing_floor_number:
                return floor
    return None


def _explicit_floor_number(value: str) -> int | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if "首层" in text or "首楼" in text or "ground floor" in text:
        return 1
    british_ordinals = {
        "first": 2,
        "second": 3,
        "third": 4,
        "fourth": 5,
        "fifth": 6,
        "sixth": 7,
        "seventh": 8,
        "eighth": 9,
        "ninth": 10,
        "tenth": 11,
    }
    ordinal_match = re.search(
        r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+floor\b",
        text,
        re.I,
    )
    if ordinal_match:
        return british_ordinals[ordinal_match.group(1).lower()]
    chinese_numbers = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    match = re.search(r"([一二三四五六七八九十])\s*(?:层|楼)", text)
    if match:
        return chinese_numbers.get(match.group(1))
    for pattern in (
        r"(?<![a-z0-9])(\d{1,2})\s*(?:层|楼)",
        r"(?<![a-z0-9])(\d{1,2})\s*f(?![a-z0-9])",
        r"(?:level|floor)\s*(\d{1,2})(?!\d)",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1))
    return None


def _should_replace_existing_levels(levels: list[dict]) -> bool:
    if not levels:
        return True
    if len(levels) == 1:
        name = str(levels[0].get("name", "")).lower()
        source = str(levels[0].get("source", "")).lower()
        status = str(levels[0].get("review_status", "")).lower()
        return "默认" in name or "default" in name or "default" in source or status == "needs_review"
    return False


def _levels_incomplete_for_floor_plans(levels: list[dict], drawings: list[dict[str, str]]) -> bool:
    explicit_plan_keys: set[str] = set()
    for drawing in drawings:
        if str(drawing.get("drawing_type") or "").lower() != "floor_plan":
            continue
        name = str(drawing.get("drawing_name") or "")
        if any(token in name.lower() for token in ("roof", "屋顶", "屋面")):
            explicit_plan_keys.add("roof")
            continue
        floor_number = _explicit_floor_number(name)
        if floor_number is not None:
            explicit_plan_keys.add(f"floor:{floor_number}")
    return len(explicit_plan_keys) >= 2 and len(levels) < len(explicit_plan_keys)


def _point_from_row(row: dict[str, str], prefix: str, z: float = 0.0) -> dict | None:
    text = _pick(row, prefix, default="")
    parsed = _parse_point(text, z=z)
    if parsed is not None:
        return parsed

    x = _to_float(_pick(row, f"{prefix}_x", f"{prefix}x", "x" if prefix in {"location", "point"} else "", default=""))
    y = _to_float(_pick(row, f"{prefix}_y", f"{prefix}y", "y" if prefix in {"location", "point"} else "", default=""))
    z_value = _to_float(_pick(row, f"{prefix}_z", f"{prefix}z", "z" if prefix in {"location", "point"} else "", default=""))
    if x is None or y is None:
        return None
    return _point(x, y, z if z_value is None else z_value)


def _level_lookup(levels: list[dict]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for level in levels:
        ident = str(level.get("id", ""))
        name = str(level.get("name", ""))
        if ident:
            lookup[ident] = name or ident
        if name:
            lookup[name] = name
    return lookup


def _resolve_level_name(value: str, lookup: dict[str, str]) -> str:
    return lookup.get(str(value), str(value))


def _parse_point(value: str, z: float = 0.0) -> dict | None:
    if not value:
        return None
    numbers = [float(item) for item in re.findall(r"[-+]?\d+(?:\.\d+)?", value)]
    if len(numbers) < 2:
        return None
    return _point(numbers[0], numbers[1], numbers[2] if len(numbers) > 2 else z)


def _parse_boundary(value: str) -> list[dict]:
    if not value:
        return []
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, list):
            points = []
            for item in parsed:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    points.append(_point(float(item[0]), float(item[1]), float(item[2]) if len(item) > 2 else 0.0))
            if points:
                return points
    except (SyntaxError, ValueError):
        pass
    chunks = re.findall(r"\(([^)]+)\)", value)
    return [point for chunk in chunks if (point := _parse_point(chunk))]


def _point(x: float, y: float, z: float) -> dict:
    return {"x": float(x), "y": float(y), "z": float(z)}


def _pick(row: dict[str, str], *keys: str, default="") -> str:
    for key in keys:
        if not key:
            continue
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _to_float(value) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return float(match.group(0))


def _to_bool(value) -> bool | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _parse_id_list(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in re.split(r"[,;|]\s*", text) if item.strip()]


def _confidence(row: dict[str, str], default: float) -> float:
    value = _to_float(_pick(row, "confidence", default=""))
    if value is None:
        return default
    return max(0.0, min(1.0, value))


def _review_status(row: dict[str, str], default: str) -> str:
    needs_review = _pick(row, "needs_review", default="")
    if str(needs_review).strip().lower() in {"true", "1", "yes", "y"}:
        return "needs_review"
    if str(needs_review).strip().lower() in {"false", "0", "no", "n"}:
        return default if default == "needs_review" else "ready"

    value = _pick(row, "review_status", "manual_review", default=default)
    if str(value).lower() in {"ok", "ready", "true", "yes", "confirmed"}:
        return "ready" if str(value).lower() != "confirmed" else "confirmed"
    if str(value).lower() in {"needs_review", "review", "false", "no", "pending"}:
        return "needs_review"
    return default


def _summarize_notes(notes: str) -> str:
    compact = " ".join(notes.split())
    return compact[:500]

