from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


Cell = tuple[Any, int]
Row = list[Cell]

STYLE_NORMAL = 0
STYLE_TITLE = 1
STYLE_SECTION = 2
STYLE_HEADER = 3
STYLE_LABEL = 4


SECTION_DEFS = [
    ("Walls", "walls"),
    ("Doors and windows", "openings"),
    ("Axes", "axes"),
    ("Stairs", "stairs"),
    ("Railings", "railings"),
    ("Elevation marks", ("plan_summary", "elevation_marks")),
    ("Floor heights", ("plan_summary", "floor_heights")),
    ("Object relations", ("relations", "object_relations")),
    ("Context summaries", ("relations", "context_summaries")),
    ("Issues", "issues"),
]


def write_single_result_workbook(path: str | Path, result: dict) -> None:
    title = drawing_label(result, 1)
    write_results_workbook(path, [(title, result)], include_overview=False)


def write_results_workbook(
    path: str | Path,
    drawing_results: list[tuple[str, dict]],
    include_overview: bool = True,
    cross_view_matches: list[dict] | None = None,
) -> None:
    sheets: list[tuple[str, list[Row]]] = []
    if include_overview:
        sheets.append(("Overview", build_overview_rows(drawing_results)))
    if cross_view_matches is not None:
        sheets.append(("Cross View Matches", build_match_rows(cross_view_matches)))
    for index, (name, result) in enumerate(drawing_results, start=1):
        sheet_name = f"{index}_{name}" if name else f"Sheet{index}"
        sheets.append((sheet_name, build_drawing_rows(result, index)))
    write_xlsx(path, sheets)


def write_human_review_workbook(path: str | Path, drawing_results: list[tuple[str, dict]]) -> None:
    sheets: list[tuple[str, list[Row]]] = []
    for index, (name, result) in enumerate(drawing_results, start=1):
        sheet_name = full_drawing_title(name, result, index)
        sheets.append((f"{index}_{sheet_name}", build_human_drawing_rows(name, result, index)))
    write_xlsx(path, sheets)


def build_human_drawing_rows(name: str, result: dict, index: int) -> list[Row]:
    notes = result.get("notes", {})
    plan = result.get("plan_summary", {})
    counts = result.get("counts", {})
    coord = result.get("coordinate_system", {})
    title = full_drawing_title(name, result, index)

    rows: list[Row] = []
    rows.append(styled_row([f"{title} - 中文识别报告"], STYLE_TITLE))
    rows.append([])
    rows.append(styled_row(["项目", "内容"], STYLE_HEADER))
    summary_items = [
        ("图纸编号", index),
        ("图纸名称", title),
        ("图纸类型", notes.get("drawing_type_name") or notes.get("drawing_type", "")),
        ("识别墙体数量", counts.get("walls", 0)),
        ("识别门窗数量", counts.get("openings", 0)),
        ("识别轴线数量", counts.get("axes", 0)),
        ("识别楼板/楼梯洞口数量", len(human_floor_opening_rows(result))),
        ("识别女儿墙数量", len(result.get("parapets", []))),
        ("识别楼梯数量", counts.get("stairs", 0)),
        ("识别栏杆数量", counts.get("railings", 0)),
        ("标高数量", len(plan.get("elevation_marks", []))),
        ("层高候选数量", len(plan.get("floor_heights", []))),
        ("文字标注数量", notes.get("text_count", 0)),
        ("需关注问题数量", counts.get("issues", 0)),
        ("局部坐标原点", coord.get("origin", "")),
        ("原点来源", coord.get("source", "")),
    ]
    rows.extend(styled_row([label, value], STYLE_NORMAL) for label, value in summary_items)

    append_human_section(
        rows,
        "轴线",
        result.get("axes", []),
        [
            ("编号", "id"),
            ("名称", "name"),
            ("起点", "local_start"),
            ("终点", "local_end"),
            ("置信度", "confidence"),
        ],
    )
    append_human_section(
        rows,
        "墙体",
        result.get("walls", []),
        [
            ("编号", "id"),
            ("起点", "local_start"),
            ("终点", "local_end"),
            ("长度mm", "length"),
            ("厚度mm", "normalized_width"),
            ("高度mm", "height_mm"),
            ("来源", "recognition_source"),
            ("置信度", "confidence"),
        ],
    )
    append_human_section(
        rows,
        "Columns",
        result.get("columns", []),
        [
            ("ID", "id"),
            ("Type", "column_type"),
            ("Center", "local_center"),
            ("Width mm", "width"),
            ("Depth mm", "depth"),
            ("Diameter mm", "diameter"),
            ("Source", "source"),
            ("Confidence", "confidence"),
        ],
    )
    append_human_section(
        rows,
        "Floors",
        result.get("floors", []),
        [
            ("ID", "id"),
            ("Type", "floor_type"),
            ("Boundary", "local_boundary_points"),
            ("Area", "area"),
            ("Thickness mm", "thickness_mm"),
            ("Opening count", "opening_count"),
            ("Source", "source"),
            ("Confidence", "confidence"),
        ],
    )
    append_human_section(
        rows,
        "女儿墙",
        result.get("parapets", []),
        [
            ("编号", "id"),
            ("类型", "parapet_type"),
            ("起点", "local_start"),
            ("终点", "local_end"),
            ("长度mm", "length"),
            ("厚度mm", "thickness_mm"),
            ("高度mm", "height_mm"),
            ("高度来源", "height_source"),
            ("关联屋顶", "host_roof_id"),
            ("识别来源", "source"),
            ("置信度", "confidence"),
            ("需复核", "needs_review"),
            ("备注", "remarks"),
        ],
    )
    append_human_section(
        rows,
        "楼板/楼梯洞口标记",
        human_floor_opening_rows(result),
        [
            ("洞口标记", "marker"),
            ("原始ID", "source_id"),
            ("洞口类型", "opening_type"),
            ("中心点", "center"),
            ("洞口边界", "boundary"),
            ("宽度mm", "width"),
            ("深度mm", "depth"),
            ("关联楼板", "host_floor_id"),
            ("关联楼梯", "related_stair_id"),
            ("识别来源", "source"),
            ("置信度", "confidence"),
            ("需复核", "needs_review"),
        ],
    )
    append_human_section(
        rows,
        "楼梯",
        result.get("stairs", []),
        [
            ("编号", "id"),
            ("类型", "stair_type"),
            ("起点", "start"),
            ("终点", "end"),
            ("楼梯/洞口边界", "stairwell_opening_boundary"),
            ("总高mm", "total_rise_mm"),
            ("总跑长mm", "total_run_mm"),
            ("梯段/平台宽mm", "width_mm"),
            ("梯井宽度mm", "stairwell_width_mm"),
            ("跑数", "run_count"),
            ("每跑级数", "risers_per_run"),
            ("每跑踏步数", "treads_per_run"),
            ("单跑长度mm", "run_length_mm"),
            ("平台长度mm", "landing_length_mm"),
            ("平台宽度mm", "landing_width_mm"),
            ("踏步高mm", "riser_height_mm"),
            ("踏步宽mm", "tread_depth_mm"),
            ("总级数", "number_of_risers"),
            ("总踏步数", "number_of_treads"),
            ("方向", "direction"),
            ("来源线数量", "source_segment_count"),
            ("置信度", "confidence"),
            ("需复核", "needs_review"),
            ("备注", "remarks"),
        ],
    )
    append_human_section(
        rows,
        "栏杆",
        result.get("railings", []),
        [
            ("编号", "id"),
            ("起点", "start"),
            ("终点", "end"),
            ("高度mm", "height_mm"),
            ("到梯井距离mm", "distance_to_stairwell_mm"),
            ("关联楼梯", "related_stair_id"),
            ("识别来源", "source"),
            ("来源线数量", "source_geometry_count"),
            ("置信度", "confidence"),
            ("需复核", "needs_review"),
            ("备注", "remarks"),
        ],
    )
    doors = [item for item in result.get("openings", []) if item.get("kind") == "door"]
    windows = [item for item in result.get("openings", []) if item.get("kind") == "window"]
    append_human_section(rows, "门", doors, opening_human_columns("门"))
    append_human_section(rows, "窗", windows, opening_human_columns("窗"))
    append_human_section(
        rows,
        "层高候选",
        plan.get("floor_heights", []),
        [
            ("楼层/位置", "label"),
            ("高度mm", "height_mm"),
            ("来源文字", "source_text"),
            ("置信度", "confidence"),
        ],
    )
    append_human_section(
        rows,
        "标高",
        plan.get("elevation_marks", []),
        [
            ("标注", "label"),
            ("标高mm", "elevation_mm"),
            ("位置", "point"),
            ("来源文字", "source_text"),
            ("置信度", "confidence"),
        ],
    )
    append_human_section(
        rows,
        "识别问题",
        result.get("issues", []),
        [
            ("构件编号", "object_id"),
            ("级别", "severity"),
            ("说明", "message"),
            ("原始值", "raw_value"),
            ("修正值", "fixed_value"),
        ],
    )
    append_human_section(
        rows,
        "文字标注",
        notes.get("text_items", []),
        [
            ("文字内容", "text"),
            ("图层", "layer"),
            ("位置", "point"),
            ("字高", "height"),
            ("旋转", "rotation"),
        ],
    )
    return rows


def human_floor_opening_rows(result: dict) -> list[dict]:
    rows: list[dict] = []
    for index, opening in enumerate(result.get("floor_openings", []), start=1):
        opening_type = opening.get("opening_type") or "rectangular_floor_opening"
        marker_prefix = "楼梯洞口" if opening_type == "stairwell_opening" else "洞口"
        rows.append(
            {
                "marker": f"{marker_prefix}-{index:03d}",
                "source_id": opening.get("id"),
                "opening_type": opening_type,
                "center": opening.get("local_center") or opening.get("center"),
                "boundary": opening.get("local_boundary_points") or opening.get("boundary_points"),
                "width": opening.get("width"),
                "depth": opening.get("depth"),
                "host_floor_id": opening.get("host_floor_id"),
                "related_stair_id": "",
                "source": opening.get("source"),
                "confidence": opening.get("confidence"),
                "needs_review": opening.get("needs_review"),
            }
        )
    for index, stair in enumerate(result.get("stairs", []), start=1):
        if not stair.get("opening_required"):
            continue
        rows.append(
            {
                "marker": f"楼梯洞口-{index:03d}",
                "source_id": f"{stair.get('id', f'STAIR{index:04d}')}-OPENING",
                "opening_type": "stairwell_opening",
                "center": stair_opening_center(stair),
                "boundary": stair.get("stairwell_opening_boundary") or stair.get("boundary_points"),
                "width": stair.get("width_mm"),
                "depth": stair.get("total_run_mm"),
                "host_floor_id": stair.get("host_floor_id"),
                "related_stair_id": stair.get("id"),
                "source": "stair_boundary",
                "confidence": stair.get("confidence"),
                "needs_review": stair.get("needs_review"),
            }
        )
    return rows


def stair_opening_center(stair: dict) -> tuple[float, float] | str:
    boundary = stair.get("stairwell_opening_boundary") or stair.get("boundary_points") or []
    points = [point for point in boundary if isinstance(point, (list, tuple)) and len(point) >= 2]
    if not points:
        return ""
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return (round((min(xs) + max(xs)) / 2.0, 3), round((min(ys) + max(ys)) / 2.0, 3))


def opening_human_columns(title: str) -> list[tuple[str, str]]:
    return [
        (f"{title}编号", "id"),
        ("中心点", "local_point"),
        ("宽度mm", "width"),
        ("高度mm", "height_mm"),
        ("所属墙体", "host_wall_id"),
        ("编号标注", "annotation"),
        ("识别来源", "source"),
        ("开启方向", "open_direction"),
        ("置信度", "confidence"),
    ]


def append_human_section(rows: list[Row], title: str, items: list[dict], columns: list[tuple[str, str]]) -> None:
    rows.append([])
    rows.append(styled_row([title], STYLE_SECTION))
    rows.append(styled_row([label for label, _key in columns], STYLE_HEADER))
    if not items:
        rows.append(styled_row(["暂无识别结果"], STYLE_NORMAL))
        return
    for item in items:
        rows.append(styled_row([friendly_value(item.get(key)) for _label, key in columns], STYLE_NORMAL))


def build_match_rows(matches: list[dict]) -> list[Row]:
    rows: list[Row] = []
    rows.append(styled_row(["Cross-view opening matches"], STYLE_TITLE))
    rows.append([])
    if not matches:
        rows.append(styled_row(["No plan/elevation opening matches were generated."], STYLE_NORMAL))
        return rows
    headers = ordered_headers(matches)
    rows.append(styled_row(headers, STYLE_HEADER))
    for item in matches:
        rows.append(styled_row([friendly_value(item.get(header)) for header in headers], STYLE_NORMAL))
    return rows


def build_overview_rows(drawing_results: list[tuple[str, dict]]) -> list[Row]:
    rows: list[Row] = []
    rows.append(styled_row(["CAD drawing extraction summary"], STYLE_TITLE))
    rows.append(styled_row(["Generated at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")], STYLE_LABEL))
    rows.append([])

    headers = [
        "index",
        "drawing",
        "drawing_type",
        "drawing_title",
        "walls",
        "doors_windows",
        "axes",
        "stairs",
        "elevation_marks",
        "floor_height_candidates",
        "issues",
    ]
    rows.append(styled_row(headers, STYLE_HEADER))
    for index, (name, result) in enumerate(drawing_results, start=1):
        counts = result.get("counts", {})
        notes = result.get("notes", {})
        plan = result.get("plan_summary", {})
        rows.append(
            styled_row(
                [
                    index,
                    name,
                    notes.get("drawing_type_name") or notes.get("drawing_type"),
                    notes.get("drawing_title") or "",
                    counts.get("walls", 0),
                    counts.get("openings", 0),
                    counts.get("axes", 0),
                    counts.get("stairs", 0),
                    len(plan.get("elevation_marks", [])),
                    len(plan.get("floor_heights", [])),
                    counts.get("issues", 0),
                ],
                STYLE_NORMAL,
            )
        )
    return rows


def build_drawing_rows(result: dict, index: int) -> list[Row]:
    notes = result.get("notes", {})
    plan = result.get("plan_summary", {})
    counts = result.get("counts", {})
    input_info = result.get("input", {})

    rows: list[Row] = []
    rows.append(styled_row([f"Drawing {index}: {notes.get('drawing_title') or input_stem(input_info) or 'Untitled'}"], STYLE_TITLE))
    rows.append(styled_row(["Original file", input_info.get("original", "")], STYLE_LABEL))
    rows.append(styled_row(["Parsed DXF", input_info.get("parsed_dxf", "")], STYLE_LABEL))
    rows.append(styled_row(["Drawing type", notes.get("drawing_type_name") or notes.get("drawing_type", "")], STYLE_LABEL))
    rows.append(styled_row(["Drawing title", notes.get("drawing_title") or ""], STYLE_LABEL))
    rows.append(styled_row(["Relative coordinate origin", str(result.get("coordinate_system", {}).get("origin", ""))], STYLE_LABEL))
    rows.append(styled_row(["Origin source", result.get("coordinate_system", {}).get("source", "")], STYLE_LABEL))
    rows.append([])

    rows.append(styled_row(["Recognition summary"], STYLE_SECTION))
    rows.append(styled_row(["item", "value"], STYLE_HEADER))
    summary_items = [
        ("walls", counts.get("walls", 0)),
        ("doors_windows", counts.get("openings", 0)),
        ("axes", counts.get("axes", 0)),
        ("stairs", counts.get("stairs", 0)),
        ("railings", counts.get("railings", 0)),
        ("elevation_marks", len(plan.get("elevation_marks", []))),
        ("floor_height_candidates", len(plan.get("floor_heights", []))),
        ("issues", counts.get("issues", 0)),
        ("cad_text_items", notes.get("text_count", 0)),
    ]
    rows.extend(styled_row([name, value], STYLE_NORMAL) for name, value in summary_items)

    for title, source in SECTION_DEFS:
        section_rows = extract_rows(result, source)
        append_section(rows, title, section_rows)

    text_items = notes.get("text_items", [])
    if text_items:
        append_section(rows, "CAD text items", text_items)
    return rows


def append_section(rows: list[Row], title: str, items: list[dict]) -> None:
    rows.append([])
    rows.append(styled_row([title], STYLE_SECTION))
    if not items:
        rows.append(styled_row(["No recognized results"], STYLE_NORMAL))
        return
    headers = ordered_headers(items)
    rows.append(styled_row(headers, STYLE_HEADER))
    for item in items:
        rows.append(styled_row([friendly_value(item.get(header)) for header in headers], STYLE_NORMAL))


def extract_rows(result: dict, source: str | tuple[str, str]) -> list[dict]:
    if isinstance(source, tuple):
        parent, child = source
        rows = result.get(parent, {}).get(child, [])
    else:
        rows = result.get(source, [])
    return [row for row in rows if isinstance(row, dict)]


def ordered_headers(items: list[dict]) -> list[str]:
    priority = [
        "id",
        "kind",
        "type",
        "name",
        "label",
        "floor",
        "source_kind",
        "start",
        "end",
        "center",
        "point",
        "boundary",
        "length",
        "width",
        "height_mm",
        "height",
        "thickness_mm",
        "normalized_width",
        "elevation_mm",
        "confidence",
        "annotation",
        "raw_text",
        "source_text",
    ]
    keys = sorted({key for item in items for key in item.keys()})
    ordered: list[str] = []
    for key in priority:
        if key in keys and key not in ordered:
            ordered.append(key)
    ordered.extend(key for key in keys if key not in ordered)
    return ordered


def drawing_label(result: dict, index: int) -> str:
    notes = result.get("notes", {})
    input_info = result.get("input", {})
    return str(notes.get("drawing_title") or input_stem(input_info) or f"Sheet{index}")


def full_drawing_title(name: str, result: dict, index: int) -> str:
    notes = result.get("notes", {})
    title = str(notes.get("drawing_title") or "").strip()
    for candidate in notes.get("drawing_title_candidates", []):
        if not isinstance(candidate, dict):
            continue
        raw = " ".join(str(candidate.get("raw_text") or "").split())
        text = " ".join(str(candidate.get("text") or "").split())
        if raw and raw != title and (not text or text == title or title in raw or text in raw):
            return raw
    return title or name or f"图纸{index}"


def input_stem(input_info: dict) -> str:
    original = input_info.get("original")
    if not original:
        return ""
    return Path(str(original)).stem


def friendly_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, (list, tuple, dict)):
        return str(value)
    return value


def styled_row(values: list[Any], style: int) -> Row:
    return [(value, style) for value in values]


def write_xlsx(path: str | Path, sheets: list[tuple[str, list[Row]]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_sheets = make_unique_sheet_names([name for name, _rows in sheets])
    with ZipFile(path, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml(len(sheets)))
        zf.writestr("_rels/.rels", root_rels_xml())
        zf.writestr("docProps/core.xml", core_props_xml())
        zf.writestr("docProps/app.xml", app_props_xml())
        zf.writestr("xl/workbook.xml", workbook_xml(safe_sheets))
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml(len(sheets)))
        zf.writestr("xl/styles.xml", styles_xml())
        for idx, (_name, rows) in enumerate(sheets, start=1):
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", sheet_xml(rows))


def make_unique_sheet_names(names: list[str]) -> list[str]:
    used: set[str] = set()
    safe: list[str] = []
    for idx, name in enumerate(names, start=1):
        base = sanitize_sheet_name(name) or f"Sheet{idx}"
        candidate = base
        suffix = 2
        while candidate.lower() in used:
            tail = f"_{suffix}"
            candidate = f"{base[:31 - len(tail)]}{tail}"
            suffix += 1
        used.add(candidate.lower())
        safe.append(candidate)
    return safe


def sanitize_sheet_name(name: str) -> str:
    value = re.sub(r"[\[\]\:\*\?\/\\]", "_", str(name)).strip()
    return value[:31]


def content_types_xml(sheet_count: int) -> str:
    sheet_overrides = "\n".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, sheet_count + 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
{sheet_overrides}
</Types>'''


def root_rels_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''


def core_props_xml() -> str:
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:creator>CAD Plan Extraction Demo</dc:creator>
<cp:lastModifiedBy>CAD Plan Extraction Demo</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:created>
<dcterms:modified xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:modified>
</cp:coreProperties>'''


def app_props_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<Application>CAD Plan Extraction Demo</Application>
</Properties>'''


def workbook_xml(sheet_names: list[str]) -> str:
    sheet_entries = "\n".join(
        f'<sheet name="{xml_text(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, name in enumerate(sheet_names, start=1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>
{sheet_entries}
</sheets>
</workbook>'''


def workbook_rels_xml(sheet_count: int) -> str:
    sheet_rels = "\n".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, sheet_count + 1)
    )
    styles_rel = f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
{sheet_rels}
{styles_rel}
</Relationships>'''


def styles_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="4">
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="15"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><name val="Calibri"/></font>
</fonts>
<fills count="6">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF1F4E79"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF0F766E"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFD9EAF7"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFF3F6FA"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left style="thin"><color rgb="FFD9D9D9"/></left><right style="thin"><color rgb="FFD9D9D9"/></right><top style="thin"><color rgb="FFD9D9D9"/></top><bottom style="thin"><color rgb="FFD9D9D9"/></bottom><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="5">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
<xf numFmtId="0" fontId="2" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
<xf numFmtId="0" fontId="3" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
<xf numFmtId="0" fontId="3" fillId="5" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def sheet_xml(rows: list[Row]) -> str:
    max_col = max((len(row) for row in rows), default=1)
    dimensions = f"A1:{column_name(max_col)}{max(len(rows), 1)}"
    cols = columns_xml(rows)
    sheet_rows = "\n".join(row_xml(row, row_index) for row_index, row in enumerate(rows, start=1))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<dimension ref="{dimensions}"/>
<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
{cols}
<sheetData>
{sheet_rows}
</sheetData>
</worksheet>'''


def columns_xml(rows: list[Row]) -> str:
    max_cols = max((len(row) for row in rows), default=1)
    widths: list[float] = []
    for col_index in range(max_cols):
        max_len = 10
        for row in rows:
            if col_index >= len(row):
                continue
            text = str(row[col_index][0] or "")
            max_len = max(max_len, min(len(text), 45))
        widths.append(min(max(max_len + 2, 10), 48))
    col_entries = "\n".join(
        f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>'
        for idx, width in enumerate(widths, start=1)
    )
    return f"<cols>\n{col_entries}\n</cols>"


def row_xml(row: Row, row_index: int) -> str:
    cells = "\n".join(cell_xml(value, style, row_index, col_index) for col_index, (value, style) in enumerate(row, start=1))
    height = ' ht="24" customHeight="1"' if any(style in {STYLE_TITLE, STYLE_SECTION} for _value, style in row) else ""
    return f'<row r="{row_index}"{height}>{cells}</row>'


def cell_xml(value: Any, style: int, row_index: int, col_index: int) -> str:
    ref = f"{column_name(col_index)}{row_index}"
    style_attr = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{style_attr}/>'
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{style_attr}><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    text = xml_text(str(value))
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{text}</t></is></c>'


def column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name or "A"


def xml_text(value: str) -> str:
    return escape(value, {'"': "&quot;"})
