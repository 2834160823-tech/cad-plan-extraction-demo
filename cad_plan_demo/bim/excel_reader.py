from __future__ import annotations

import csv
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


SHEET_ALIASES = {
    "levels": "levels",
    "level": "levels",
    "标高": "levels",
    "楼层": "levels",
    "grids": "grids",
    "axes": "grids",
    "轴网": "grids",
    "轴线": "grids",
    "walls": "walls",
    "墙": "walls",
    "墙体": "walls",
    "columns": "columns",
    "column": "columns",
    "柱": "columns",
    "柱子": "columns",
    "slabs": "slabs",
    "floors": "slabs",
    "楼板": "slabs",
    "floor_openings": "floor_openings",
    "floor openings": "floor_openings",
    "slab_openings": "floor_openings",
    "slab openings": "floor_openings",
    "holes": "floor_openings",
    "洞口": "floor_openings",
    "stairs": "stairs",
    "stair": "stairs",
    "楼梯": "stairs",
    "妤兼": "stairs",
    "doors": "doors",
    "门": "doors",
    "windows": "windows",
    "窗": "windows",
}

EXPECTED_SHEETS = ("levels", "grids", "columns", "walls", "slabs", "floor_openings", "stairs", "doors", "windows")

NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def read_fixed_excel(path: str | Path) -> dict[str, list[dict[str, str]]]:
    """Read the fixed BIM workbook.

    The first row of each sheet is treated as headers. Empty rows are skipped.
    This lightweight reader supports normal .xlsx files without requiring
    openpyxl, which keeps the demo easy to run on a clean machine.
    """

    path = Path(path)
    if path.suffix.lower() == ".csv":
        return _read_csv_file(path)
    if path.is_dir():
        return _read_csv_folder(path)
    if path.suffix.lower() != ".xlsx":
        raise ValueError("Input must be a .xlsx workbook, a .csv file, or a folder of CSV sheets.")

    with zipfile.ZipFile(path) as zf:
        shared_strings = _read_shared_strings(zf)
        sheet_paths = _find_sheet_paths(zf)
        workbook: dict[str, list[dict[str, str]]] = {name: [] for name in EXPECTED_SHEETS}
        report_sheets: dict[str, list[list[str]]] = {}
        for raw_name, sheet_path in sheet_paths.items():
            rows = _read_sheet_rows(zf, sheet_path, shared_strings)
            report_sheets[raw_name] = rows
            canonical = _canonical_sheet_name(raw_name)
            if canonical is None:
                continue
            workbook[canonical].extend(_rows_to_dicts(rows))

        report_workbook = _read_project_report_sheets(report_sheets)
        for name, rows in report_workbook.items():
            workbook[name].extend(rows)
    return workbook


def read_design_notes(path: str | Path) -> str:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_csv_file(path: Path) -> dict[str, list[dict[str, str]]]:
    canonical = _canonical_sheet_name(path.stem) or "walls"
    return _with_empty_sheets({canonical: _read_csv_rows(path)})


def _read_csv_folder(path: Path) -> dict[str, list[dict[str, str]]]:
    workbook: dict[str, list[dict[str, str]]] = {name: [] for name in EXPECTED_SHEETS}
    for csv_path in path.glob("*.csv"):
        canonical = _canonical_sheet_name(csv_path.stem)
        if canonical:
            workbook[canonical].extend(_read_csv_rows(csv_path))
    return workbook


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def _with_empty_sheets(found: dict[str, list[dict[str, str]]]) -> dict[str, list[dict[str, str]]]:
    workbook = {name: [] for name in EXPECTED_SHEETS}
    workbook.update(found)
    return workbook


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values: list[str] = []
    for si in root.findall("main:si", NS):
        pieces = [node.text or "" for node in si.findall(".//main:t", NS)]
        values.append("".join(pieces))
    return values


def _find_sheet_paths(zf: zipfile.ZipFile) -> dict[str, str]:
    workbook_root = ET.fromstring(zf.read("xl/workbook.xml"))
    rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rels = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rel_root.findall("pkgrel:Relationship", NS)
    }

    paths: dict[str, str] = {}
    for sheet in workbook_root.findall("main:sheets/main:sheet", NS):
        name = sheet.attrib.get("name", "")
        rid = sheet.attrib.get(f"{{{NS['rel']}}}id", "")
        target = rels.get(rid)
        if not target:
            continue
        clean = target.lstrip("/")
        if not clean.startswith("xl/"):
            clean = "xl/" + clean
        paths[name] = clean
    return paths


def _read_sheet_rows(zf: zipfile.ZipFile, sheet_path: str, shared_strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(zf.read(sheet_path))
    rows: list[list[str]] = []
    for row in root.findall(".//main:sheetData/main:row", NS):
        values: dict[int, str] = {}
        for cell in row.findall("main:c", NS):
            ref = cell.attrib.get("r", "")
            col_index = _column_index(ref)
            values[col_index] = _cell_value(cell, shared_strings)
        if values:
            max_col = max(values)
            rows.append([values.get(i, "") for i in range(max_col + 1)])
    return rows


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        pieces = [node.text or "" for node in cell.findall(".//main:t", NS)]
        return "".join(pieces).strip()

    value_node = cell.find("main:v", NS)
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text.strip()
    if cell_type == "s":
        try:
            return shared_strings[int(raw)].strip()
        except (IndexError, ValueError):
            return ""
    return raw


def _rows_to_dicts(rows: list[list[str]]) -> list[dict[str, str]]:
    if not rows:
        return []
    headers = [_normalize_header(value) for value in rows[0]]
    result: list[dict[str, str]] = []
    for row in rows[1:]:
        item = {
            headers[i]: (row[i].strip() if i < len(row) else "")
            for i in range(len(headers))
            if headers[i]
        }
        if any(value for value in item.values()):
            result.append(item)
    return result


def _read_project_report_sheets(sheets: dict[str, list[list[str]]]) -> dict[str, list[dict[str, str]]]:
    """Read the human-oriented CAD project_report.xlsx layout.

    The CAD recognizer writes one worksheet per drawing, with repeated sections
    such as "墙体 walls" and "门窗 doors/windows". This adapter extracts those
    embedded tables into the same logical sheets used by the BIM agent.
    """

    workbook: dict[str, list[dict[str, str]]] = {name: [] for name in EXPECTED_SHEETS}
    for sheet_name, rows in sheets.items():
        i = 0
        while i < len(rows):
            section = _canonical_report_section(rows[i][0] if rows[i] else "")
            if section is None:
                i += 1
                continue

            if i + 1 >= len(rows) or _is_no_result_row(rows[i + 1]):
                i += 2
                continue

            headers = [_normalize_header(value) for value in rows[i + 1]]
            j = i + 2
            while j < len(rows):
                first_cell = rows[j][0] if rows[j] else ""
                if _canonical_report_section(first_cell) is not None or _is_no_result_row(rows[j]):
                    break
                item = _row_to_dict(headers, rows[j])
                if item:
                    item.setdefault("source", f"project_report:{sheet_name}:{section}")
                    _append_report_item(workbook, section, item)
                j += 1
            i = j
    return workbook


def _row_to_dict(headers: list[str], row: list[str]) -> dict[str, str]:
    item = {
        headers[i]: (row[i].strip() if i < len(row) else "")
        for i in range(len(headers))
        if headers[i]
    }
    return item if any(value for value in item.values()) else {}


def _append_report_item(workbook: dict[str, list[dict[str, str]]], section: str, item: dict[str, str]) -> None:
    if section == "openings":
        kind = item.get("kind", "").strip().lower()
        if kind == "window":
            workbook["windows"].append(item)
        else:
            workbook["doors"].append(item)
        return
    workbook[section].append(item)


def _canonical_report_section(value: str) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if "doors/windows" in text or "door/window" in text:
        return "openings"
    if "walls" in text or "墙体" in text:
        return "walls"
    if "axes" in text or "轴线 axes" in text or "轴网 axes" in text:
        return "grids"
    if "slabs" in text or "floors" in text or "楼板" in text:
        return "slabs"
    if "stairs" in text or "stair" in text or "楼梯" in text or "妤兼" in text:
        return "stairs"
    return None


def _is_no_result_row(row: list[str]) -> bool:
    return bool(row) and str(row[0]).strip() in {"无识别结果", "no results", "no result"}


def _normalize_header(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _canonical_sheet_name(raw: str) -> str | None:
    normalized = raw.strip().lower().replace(" ", "_")
    if normalized in SHEET_ALIASES:
        return SHEET_ALIASES[normalized]
    return SHEET_ALIASES.get(raw.strip())


def _column_index(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", cell_ref.upper())
    if not match:
        return 0
    value = 0
    for char in match.group(1):
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1
