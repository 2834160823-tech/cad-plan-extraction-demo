from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, cos, degrees, radians, sin
from pathlib import Path


@dataclass
class CadEntity:
    type: str
    layer: str = "0"
    data: dict = field(default_factory=dict)


def _as_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _read_tags(path: Path) -> list[tuple[str, str]]:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    tags: list[tuple[str, str]] = []
    i = 0
    while i + 1 < len(lines):
        tags.append((lines[i].strip(), lines[i + 1].strip()))
        i += 2
    return tags


def parse_dxf(path: str | Path) -> list[CadEntity]:
    """Parse a small, practical subset of DXF entities.

    This demo intentionally supports common 2D plan entities only:
    LINE, ARC, CIRCLE, LWPOLYLINE, POLYLINE/VERTEX/SEQEND, TEXT, MTEXT,
    DIMENSION, INSERT.
    It is enough for a first CAD-to-object extraction demo and can be
    replaced later by ezdxf for full DXF coverage.
    """

    path = Path(path)
    tags = _read_tags(path)
    entities: list[CadEntity] = []
    in_entities = False
    i = 0

    while i < len(tags):
        code, value = tags[i]
        if code == "0" and value == "SECTION":
            if i + 1 < len(tags) and tags[i + 1] == ("2", "ENTITIES"):
                in_entities = True
                i += 2
                continue
        if code == "0" and value == "ENDSEC" and in_entities:
            in_entities = False
        if not in_entities or code != "0":
            i += 1
            continue

        entity_type = value.upper()
        if entity_type in {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "TEXT", "MTEXT", "DIMENSION", "INSERT"}:
            next_i = i + 1
            entity_tags: list[tuple[str, str]] = []
            while next_i < len(tags) and tags[next_i][0] != "0":
                entity_tags.append(tags[next_i])
                next_i += 1
            parsed = _parse_simple_entity(entity_type, entity_tags)
            if parsed is not None:
                entities.append(parsed)
            i = next_i
            continue

        if entity_type == "POLYLINE":
            parsed, next_i = _parse_polyline(tags, i)
            if parsed is not None:
                entities.append(parsed)
            i = next_i
            continue

        i += 1

    # Resolve every INSERT with the DXF block transform. Some drawings keep
    # world-like coordinates inside BLOCK definitions, so using the insertion
    # point alone can displace doors and stairs by an entire plan module.
    return _resolved_block_references(tags, entities)


def _resolved_block_references(
    tags: list[tuple[str, str]],
    model_entities: list[CadEntity],
) -> list[CadEntity]:
    definitions = _parse_block_definitions(tags)
    resolved: list[CadEntity] = []
    for entity in model_entities:
        if entity.type != "INSERT":
            resolved.append(entity)
            continue
        definition = definitions.get(str(entity.data.get("name", "")))
        if definition is None or not definition.get("entities"):
            resolved.append(entity)
            continue
        transformed = [
            item
            for child in definition["entities"]
            if (item := _transform_block_entity(child, entity.data, definition["base_point"])) is not None
        ]
        points = [point for child in transformed for point in _entity_points(child)]
        if not points:
            resolved.append(entity)
            continue
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        bounds = (min(xs), min(ys), max(xs), max(ys))
        center = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
        data = dict(entity.data)
        data.update(
            {
                "point": center,
                "block_bounds": bounds,
                "block_layers": definition["layers"],
                "block_geometry_count": len(transformed),
                "source_insert_point": tuple(entity.data.get("point", (0.0, 0.0))),
                "block_entities": [
                    {"type": child.type, "layer": child.layer, "data": child.data}
                    for child in transformed
                ],
            }
        )
        resolved.append(CadEntity("INSERT", entity.layer, data))
    return resolved


def _transform_block_point(
    point: tuple[float, float],
    insert_data: dict,
    base_point: tuple[float, float],
) -> tuple[float, float]:
    insert = tuple(insert_data.get("point", (0.0, 0.0)))
    scale = tuple(insert_data.get("scale", (1.0, 1.0)))
    angle = radians(float(insert_data.get("rotation", 0.0) or 0.0))
    x = (point[0] - base_point[0]) * float(scale[0])
    y = (point[1] - base_point[1]) * float(scale[1])
    return (
        insert[0] + x * cos(angle) - y * sin(angle),
        insert[1] + x * sin(angle) + y * cos(angle),
    )


def _transform_block_entity(
    entity: CadEntity,
    insert_data: dict,
    base_point: tuple[float, float],
) -> CadEntity | None:
    data = dict(entity.data)
    if entity.type == "LINE":
        data["start"] = _transform_block_point(tuple(data["start"]), insert_data, base_point)
        data["end"] = _transform_block_point(tuple(data["end"]), insert_data, base_point)
    elif entity.type in {"LWPOLYLINE", "POLYLINE"}:
        data["points"] = [
            _transform_block_point(tuple(point), insert_data, base_point)
            for point in data.get("points", [])
        ]
    elif entity.type in {"ARC", "CIRCLE"}:
        center = tuple(data["center"])
        data["center"] = _transform_block_point(center, insert_data, base_point)
        scale = tuple(insert_data.get("scale", (1.0, 1.0)))
        data["radius"] = float(data.get("radius", 0.0) or 0.0) * (
            abs(float(scale[0])) + abs(float(scale[1]))
        ) / 2.0
        if entity.type == "ARC":
            start = _transform_angle(float(data.get("start_angle", 0.0)), insert_data)
            end = _transform_angle(float(data.get("end_angle", 0.0)), insert_data)
            determinant = float(scale[0]) * float(scale[1])
            data["start_angle"], data["end_angle"] = (end, start) if determinant < 0 else (start, end)
    elif entity.type in {"TEXT", "MTEXT", "INSERT"}:
        data["point"] = _transform_block_point(tuple(data.get("point", (0.0, 0.0))), insert_data, base_point)
    else:
        return None
    return CadEntity(entity.type, entity.layer, data)


def _transform_angle(angle_degrees: float, insert_data: dict) -> float:
    angle = radians(angle_degrees)
    scale = tuple(insert_data.get("scale", (1.0, 1.0)))
    x = cos(angle) * float(scale[0])
    y = sin(angle) * float(scale[1])
    rotation = radians(float(insert_data.get("rotation", 0.0) or 0.0))
    tx = x * cos(rotation) - y * sin(rotation)
    ty = x * sin(rotation) + y * cos(rotation)
    return degrees(atan2(ty, tx)) % 360


def _world_coordinate_block_references(
    tags: list[tuple[str, str]],
    model_entities: list[CadEntity],
) -> list[CadEntity]:
    definitions = _parse_block_definitions(tags)
    references: list[CadEntity] = []
    for insert in model_entities:
        if insert.type != "INSERT":
            continue
        name = str(insert.data.get("name", ""))
        definition = definitions.get(name)
        if definition is None:
            continue
        point = tuple(insert.data.get("point", (0.0, 0.0)))
        min_x, min_y, max_x, max_y = definition["bounds"]
        center = ((min_x + max_x) / 2, (min_y + max_y) / 2)
        # Some definitions already carry their world position while nested
        # INSERT references carry only a local floor/module offset such as
        # (0, 3300).  Treat small offsets as translated copies, but skip normal
        # model-space inserts whose point is already a large world coordinate.
        if distance_2d(center, (0.0, 0.0)) < 1.0 or distance_2d(point, (0.0, 0.0)) > 20000.0:
            continue
        translated_bounds = translate_bounds(definition["bounds"], point)
        translated_center = (center[0] + point[0], center[1] + point[1])
        data = dict(insert.data)
        data.update(
            {
                "point": translated_center,
                "block_bounds": translated_bounds,
                "block_layers": definition["layers"],
                "block_geometry_count": definition["geometry_count"],
                "source_insert_point": point,
            }
        )
        references.append(CadEntity("INSERT", insert.layer, data))
    return references


def translate_bounds(
    bounds: tuple[float, float, float, float],
    offset: tuple[float, float],
) -> tuple[float, float, float, float]:
    return (
        bounds[0] + offset[0],
        bounds[1] + offset[1],
        bounds[2] + offset[0],
        bounds[3] + offset[1],
    )


def _parse_block_definitions(tags: list[tuple[str, str]]) -> dict[str, dict]:
    definitions: dict[str, dict] = {}
    in_blocks = False
    i = 0
    while i < len(tags):
        code, value = tags[i]
        if code == "0" and value == "SECTION":
            in_blocks = i + 1 < len(tags) and tags[i + 1] == ("2", "BLOCKS")
            i += 1
        elif code == "0" and value == "ENDSEC":
            in_blocks = False
        elif in_blocks and code == "0" and value == "BLOCK":
            end = i + 1
            while end < len(tags) and not (tags[end] == ("0", "ENDBLK")):
                end += 1
            block_tags = tags[i + 1:end]
            name = next((item_value for item_code, item_value in block_tags if item_code == "2"), "")
            header: list[tuple[str, str]] = []
            for item in block_tags:
                if item[0] == "0":
                    break
                header.append(item)
            base_x = next((_as_float(item_value) for item_code, item_value in header if item_code == "10"), 0.0)
            base_y = next((_as_float(item_value) for item_code, item_value in header if item_code == "20"), 0.0)
            entities = _parse_entities_from_tags(block_tags)
            points = [point for entity in entities for point in _entity_points(entity)]
            if name and points:
                xs = [point[0] for point in points]
                ys = [point[1] for point in points]
                definitions[name] = {
                    "bounds": (min(xs), min(ys), max(xs), max(ys)),
                    "layers": sorted({entity.layer for entity in entities}),
                    "geometry_count": len(entities),
                    "base_point": (base_x, base_y),
                    "entities": entities,
                }
            i = end
        i += 1
    return definitions


def _parse_entities_from_tags(tags: list[tuple[str, str]]) -> list[CadEntity]:
    entities: list[CadEntity] = []
    i = 0
    supported = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "TEXT", "MTEXT", "DIMENSION", "INSERT"}
    while i < len(tags):
        code, value = tags[i]
        if code != "0" or value.upper() not in supported:
            i += 1
            continue
        entity_type = value.upper()
        next_i = i + 1
        entity_tags: list[tuple[str, str]] = []
        while next_i < len(tags) and tags[next_i][0] != "0":
            entity_tags.append(tags[next_i])
            next_i += 1
        parsed = _parse_simple_entity(entity_type, entity_tags)
        if parsed is not None:
            entities.append(parsed)
        i = next_i
    return entities


def _entity_points(entity: CadEntity) -> list[tuple[float, float]]:
    if entity.type == "LINE":
        return [tuple(entity.data.get("start", (0.0, 0.0))), tuple(entity.data.get("end", (0.0, 0.0)))]
    if entity.type in {"CIRCLE", "ARC"}:
        center = tuple(entity.data.get("center", (0.0, 0.0)))
        radius = float(entity.data.get("radius", 0.0) or 0.0)
        return [(center[0] - radius, center[1] - radius), (center[0] + radius, center[1] + radius)]
    if entity.type == "LWPOLYLINE":
        return [tuple(point) for point in entity.data.get("points", [])]
    return []


def distance_2d(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _parse_simple_entity(entity_type: str, tags: list[tuple[str, str]]) -> CadEntity | None:
    layer = "0"
    data: dict = {}

    if entity_type == "LINE":
        x1 = y1 = x2 = y2 = 0.0
        for code, value in tags:
            if code == "8":
                layer = value
            elif code == "10":
                x1 = _as_float(value)
            elif code == "20":
                y1 = _as_float(value)
            elif code == "11":
                x2 = _as_float(value)
            elif code == "21":
                y2 = _as_float(value)
            elif code == "6":
                data["linetype"] = value
        data.update({"start": (x1, y1), "end": (x2, y2)})
        return CadEntity(entity_type, layer, data)

    if entity_type == "CIRCLE":
        x = y = radius = 0.0
        for code, value in tags:
            if code == "8":
                layer = value
            elif code == "10":
                x = _as_float(value)
            elif code == "20":
                y = _as_float(value)
            elif code == "40":
                radius = _as_float(value)
        if radius > 0:
            data.update({"center": (x, y), "radius": radius})
            return CadEntity(entity_type, layer, data)
        return None

    if entity_type == "ARC":
        x = y = radius = start_angle = end_angle = 0.0
        for code, value in tags:
            if code == "8":
                layer = value
            elif code == "10":
                x = _as_float(value)
            elif code == "20":
                y = _as_float(value)
            elif code == "40":
                radius = _as_float(value)
            elif code == "50":
                start_angle = _as_float(value)
            elif code == "51":
                end_angle = _as_float(value)
        if radius > 0:
            data.update({"center": (x, y), "radius": radius, "start_angle": start_angle, "end_angle": end_angle})
            return CadEntity(entity_type, layer, data)
        return None

    if entity_type == "LWPOLYLINE":
        points: list[tuple[float, float]] = []
        current_x: float | None = None
        closed = False
        for code, value in tags:
            if code == "8":
                layer = value
            elif code == "70":
                closed = bool(_as_int(value) & 1)
            elif code == "10":
                current_x = _as_float(value)
            elif code == "20" and current_x is not None:
                points.append((current_x, _as_float(value)))
                current_x = None
        if len(points) >= 2:
            data.update({"points": points, "closed": closed})
            return CadEntity(entity_type, layer, data)
        return None

    if entity_type in {"TEXT", "MTEXT"}:
        x = y = rotation = height = 0.0
        text_parts: list[str] = []
        for code, value in tags:
            if code == "8":
                layer = value
            elif code == "1":
                text_parts.append(value)
            elif code == "3":
                text_parts.append(value)
            elif code == "10":
                x = _as_float(value)
            elif code == "20":
                y = _as_float(value)
            elif code == "40":
                height = _as_float(value)
            elif code == "50":
                rotation = _as_float(value)
        data.update({"text": "".join(text_parts), "point": (x, y), "height": height, "rotation": rotation})
        return CadEntity(entity_type, layer, data)

    if entity_type == "DIMENSION":
        x = y = text_x = text_y = rotation = measurement = 0.0
        p1x = p1y = p2x = p2y = 0.0
        text_parts: list[str] = []
        name = ""
        for code, value in tags:
            if code == "8":
                layer = value
            elif code == "1":
                text_parts.append(value)
            elif code == "2":
                name = value
            elif code == "10":
                x = _as_float(value)
            elif code == "20":
                y = _as_float(value)
            elif code == "11":
                text_x = _as_float(value)
            elif code == "21":
                text_y = _as_float(value)
            elif code == "13":
                p1x = _as_float(value)
            elif code == "23":
                p1y = _as_float(value)
            elif code == "14":
                p2x = _as_float(value)
            elif code == "24":
                p2y = _as_float(value)
            elif code == "42":
                measurement = _as_float(value)
            elif code == "50":
                rotation = _as_float(value)
        data.update(
            {
                "name": name,
                "text": "".join(text_parts),
                "point": (x, y),
                "text_point": (text_x, text_y),
                "measurement": measurement,
                "start": (p1x, p1y),
                "end": (p2x, p2y),
                "rotation": rotation,
            }
        )
        return CadEntity(entity_type, layer, data)

    if entity_type == "INSERT":
        x = y = rotation = 0.0
        name = ""
        sx = sy = 1.0
        for code, value in tags:
            if code == "8":
                layer = value
            elif code == "2":
                name = value
            elif code == "10":
                x = _as_float(value)
            elif code == "20":
                y = _as_float(value)
            elif code == "41":
                sx = _as_float(value, 1.0)
            elif code == "42":
                sy = _as_float(value, 1.0)
            elif code == "50":
                rotation = _as_float(value)
        data.update({"name": name, "point": (x, y), "scale": (sx, sy), "rotation": rotation})
        return CadEntity(entity_type, layer, data)

    return None


def _parse_polyline(tags: list[tuple[str, str]], start_index: int) -> tuple[CadEntity | None, int]:
    layer = "0"
    closed = False
    points: list[tuple[float, float]] = []
    i = start_index + 1
    while i < len(tags) and tags[i][0] != "0":
        code, value = tags[i]
        if code == "8":
            layer = value
        elif code == "70":
            closed = bool(_as_int(value) & 1)
        i += 1

    while i < len(tags):
        code, value = tags[i]
        if code == "0" and value.upper() == "SEQEND":
            i += 1
            break
        if code == "0" and value.upper() == "VERTEX":
            vx = vy = 0.0
            i += 1
            while i < len(tags) and tags[i][0] != "0":
                c, v = tags[i]
                if c == "10":
                    vx = _as_float(v)
                elif c == "20":
                    vy = _as_float(v)
                i += 1
            points.append((vx, vy))
            continue
        if code == "0":
            break
        i += 1

    if len(points) >= 2:
        return CadEntity("POLYLINE", layer, {"points": points, "closed": closed}), i
    return None, i
