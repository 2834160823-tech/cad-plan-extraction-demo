from __future__ import annotations

from .schema import REVIEW_STATUSES, SUPPORTED_COMPONENTS, SCHEMA_VERSION


def validate_standard_model(model: dict) -> list[dict]:
    issues: list[dict] = []
    if model.get("schema_version") != SCHEMA_VERSION:
        issues.append(_issue("model", "schema_version", "error", f"Expected schema_version {SCHEMA_VERSION}."))

    components = model.get("components", {})
    for group in SUPPORTED_COMPONENTS:
        if group not in components or not isinstance(components.get(group), list):
            issues.append(_issue(group, "", "error", "Component group is missing or not a list."))

    _validate_levels(components.get("levels", []), issues)
    _validate_grids(components.get("grids", []), issues)
    _validate_columns(components.get("columns", []), issues)
    _validate_walls(components.get("walls", []), issues)
    _validate_slabs(components.get("slabs", []), issues)
    _validate_floor_openings(components.get("floor_openings", []), issues)
    _validate_stairs(components.get("stairs", []), issues)
    _validate_openings(components.get("doors", []), "doors", issues)
    _validate_openings(components.get("windows", []), "windows", issues)

    wall_ids = {item.get("id") for item in components.get("walls", [])}
    for group in ("doors", "windows"):
        for item in components.get(group, []):
            host = item.get("host_wall_id")
            if not host:
                issues.append(_issue(group, item.get("id", ""), "needs_review", "Missing host_wall_id."))
            elif host not in wall_ids:
                issues.append(_issue(group, item.get("id", ""), "needs_review", f"Host wall {host} was not found."))

    slab_ids = {item.get("id") for item in components.get("slabs", [])}
    for item in components.get("floor_openings", []):
        host = item.get("host_floor_id")
        if not host:
            issues.append(_issue("floor_openings", item.get("id", ""), "needs_review", "Missing host_floor_id."))
        elif host not in slab_ids:
            issues.append(_issue("floor_openings", item.get("id", ""), "needs_review", f"Host floor {host} was not found."))

    floor_opening_ids = {item.get("id") for item in components.get("floor_openings", [])}
    for item in components.get("stairs", []):
        opening_id = item.get("matched_floor_opening_id")
        if opening_id and opening_id not in floor_opening_ids:
            issues.append(_issue("stairs", item.get("id", ""), "needs_review", f"Matched floor opening {opening_id} was not found."))

    return issues


def apply_validation(model: dict) -> dict:
    issues = validate_standard_model(model)
    components = model.setdefault("components", {})
    issue_keys = {(item["component_group"], item["component_id"]) for item in issues if item["component_id"]}
    for group, items in components.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if item.get("review_status") not in REVIEW_STATUSES:
                item["review_status"] = "needs_review"
            if (group, item.get("id")) in issue_keys and item.get("review_status") == "ready":
                item["review_status"] = "needs_review"

    model.setdefault("validation", {})
    model["validation"]["issues"] = issues
    model["validation"]["requires_human_confirmation"] = bool(
        issues or any(
            item.get("review_status") == "needs_review"
            for items in components.values()
            if isinstance(items, list)
            for item in items
        )
    )
    model["validation"].setdefault("model_sequence", ["levels", "grids", "columns", "walls", "slabs", "floor_openings", "doors", "windows"])
    return model


def _validate_levels(items: list, issues: list[dict]) -> None:
    seen_names: set[str] = set()
    for item in items:
        _common(item, "levels", issues)
        name = item.get("name")
        if not name:
            issues.append(_issue("levels", item.get("id", ""), "error", "Missing level name."))
        elif name in seen_names:
            issues.append(_issue("levels", item.get("id", ""), "needs_review", f"Duplicate level name {name}."))
        seen_names.add(name)
        _number(item, "elevation_mm", "levels", issues, required=True)


def _validate_grids(items: list, issues: list[dict]) -> None:
    for item in items:
        _common(item, "grids", issues)
        _point(item, "start", "grids", issues)
        _point(item, "end", "grids", issues)


def _validate_walls(items: list, issues: list[dict]) -> None:
    for item in items:
        _common(item, "walls", issues)
        _point(item, "start", "walls", issues)
        _point(item, "end", "walls", issues)
        if not item.get("base_level"):
            issues.append(_issue("walls", item.get("id", ""), "error", "Missing base_level."))
        if item.get("height_mm") is None and not item.get("top_level"):
            issues.append(_issue("walls", item.get("id", ""), "needs_review", "Missing both height_mm and top_level."))


def _validate_columns(items: list, issues: list[dict]) -> None:
    for item in items:
        _common(item, "columns", issues)
        _point(item, "location", "columns", issues)
        if not item.get("level"):
            issues.append(_issue("columns", item.get("id", ""), "error", "Missing level."))
        has_rect_size = isinstance(item.get("width_mm"), (int, float)) and isinstance(item.get("depth_mm"), (int, float))
        has_diameter = isinstance(item.get("diameter_mm"), (int, float))
        if not has_rect_size and not has_diameter:
            issues.append(_issue("columns", item.get("id", ""), "needs_review", "Column needs width/depth or diameter."))
        if item.get("height_mm") is None and item.get("top_z_mm") is None and not item.get("top_level"):
            issues.append(_issue("columns", item.get("id", ""), "needs_review", "Column needs height_mm, top_z_mm, or top_level."))


def _validate_slabs(items: list, issues: list[dict]) -> None:
    for item in items:
        _common(item, "slabs", issues)
        boundary = item.get("boundary")
        if not isinstance(boundary, list) or len(boundary) < 3:
            issues.append(_issue("slabs", item.get("id", ""), "needs_review", "Slab boundary needs at least three points."))


def _validate_floor_openings(items: list, issues: list[dict]) -> None:
    for item in items:
        _common(item, "floor_openings", issues)
        boundary = item.get("boundary")
        if not isinstance(boundary, list) or len(boundary) < 3:
            issues.append(_issue("floor_openings", item.get("id", ""), "needs_review", "Floor opening boundary needs at least three points."))


def _validate_stairs(items: list, issues: list[dict]) -> None:
    for item in items:
        _common(item, "stairs", issues)
        boundary = item.get("boundary")
        if not isinstance(boundary, list) or len(boundary) < 3:
            issues.append(_issue("stairs", item.get("id", ""), "needs_review", "Stair boundary needs at least three points."))
        if not item.get("base_level") or not item.get("top_level"):
            issues.append(_issue("stairs", item.get("id", ""), "needs_review", "Stair needs base_level and top_level."))
        for field in ("riser_height_mm", "tread_depth_mm", "run_count", "number_of_risers"):
            _number(item, field, "stairs", issues, required=False)


def _validate_openings(items: list, group: str, issues: list[dict]) -> None:
    for item in items:
        _common(item, group, issues)
        _point(item, "location", group, issues)
        _number(item, "width_mm", group, issues, required=True)
        _number(item, "height_mm", group, issues, required=(group == "windows"))
        if group == "doors" and item.get("height_mm") is None:
            issues.append(_issue(group, item.get("id", ""), "needs_review", "Missing height_mm; Revit tool may default reviewed doors to 2100 mm."))


def _common(item: dict, group: str, issues: list[dict]) -> None:
    component_id = item.get("id", "")
    if not component_id:
        issues.append(_issue(group, "", "error", "Missing component id."))
    if item.get("review_status") not in REVIEW_STATUSES:
        issues.append(_issue(group, component_id, "needs_review", "Invalid review_status."))
    confidence = item.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        issues.append(_issue(group, component_id, "needs_review", "Confidence must be between 0 and 1."))


def _point(item: dict, field: str, group: str, issues: list[dict]) -> None:
    value = item.get(field)
    if not isinstance(value, dict) or any(not isinstance(value.get(axis), (int, float)) for axis in ("x", "y", "z")):
        issues.append(_issue(group, item.get("id", ""), "error", f"Invalid point field {field}."))


def _number(item: dict, field: str, group: str, issues: list[dict], *, required: bool) -> None:
    value = item.get(field)
    if value is None and not required:
        return
    if not isinstance(value, (int, float)):
        severity = "error" if required else "needs_review"
        issues.append(_issue(group, item.get("id", ""), severity, f"Invalid numeric field {field}."))


def _issue(group: str, component_id: str, severity: str, message: str) -> dict:
    return {
        "component_group": group,
        "component_id": component_id,
        "severity": severity,
        "message": message,
    }
