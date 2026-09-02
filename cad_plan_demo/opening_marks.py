from __future__ import annotations

import re
from typing import Any


MARK_PREFIX_KIND = {
    "D": "door",
    "M": "door",
    "TLM": "door",
    "SD": "door",
    "DD": "door",
    "DOOR": "door",
    "C": "window",
    "W": "window",
    "TLC": "window",
    "PKC": "window",
    "SW": "window",
    "CW": "window",
    "WIN": "window",
    "WINDOW": "window",
}

PREFIX_PATTERN = "|".join(sorted(MARK_PREFIX_KIND, key=len, reverse=True))
PREFIXED_MARK_RE = re.compile(
    rf"(?<![A-Z0-9])(?P<code>{PREFIX_PATTERN})[-_\s]*(?P<width>\d{{2}})[-_\s]*(?P<height>\d{{2}})(?![A-Z0-9])",
    re.I,
)
UNPREFIXED_MARK_RE = re.compile(r"(?<!\d)(?P<width>\d{2})[-_\s]*(?P<height>\d{2})(?!\d)")


def normalize_mark_text(value: object) -> str:
    text = str(value or "").upper()
    text = re.sub(r"\{[^;]*;", "", text)
    text = text.replace("}", "")
    text = text.replace("\\P", " ")
    return re.sub(r"[^A-Z0-9]+", " ", text)


def extract_mark_dimensions(
    value: object,
    *,
    kind_hint: str | None = None,
    allow_unprefixed: bool = False,
) -> dict[str, Any] | None:
    text = normalize_mark_text(value)
    for match in PREFIXED_MARK_RE.finditer(text):
        code = match.group("code").upper()
        kind = MARK_PREFIX_KIND.get(code)
        if kind_hint and kind and kind != kind_hint:
            continue
        parsed = _dimension_record(match, kind or kind_hint, match.group(0), "prefixed_mark")
        if parsed is not None:
            return parsed
    if allow_unprefixed:
        for match in UNPREFIXED_MARK_RE.finditer(text):
            parsed = _dimension_record(match, kind_hint, match.group(0), "unprefixed_mark")
            if parsed is not None:
                return parsed
    return None


def iter_mark_dimensions(
    value: object,
    *,
    kind_hint: str | None = None,
    allow_unprefixed: bool = False,
) -> list[dict[str, Any]]:
    text = normalize_mark_text(value)
    records: list[dict[str, Any]] = []
    for match in PREFIXED_MARK_RE.finditer(text):
        code = match.group("code").upper()
        kind = MARK_PREFIX_KIND.get(code)
        if kind_hint and kind and kind != kind_hint:
            continue
        parsed = _dimension_record(match, kind or kind_hint, match.group(0), "prefixed_mark")
        if parsed is not None:
            records.append(parsed)
    if allow_unprefixed:
        for match in UNPREFIXED_MARK_RE.finditer(text):
            parsed = _dimension_record(match, kind_hint, match.group(0), "unprefixed_mark")
            if parsed is not None:
                records.append(parsed)
    return records


def _dimension_record(match: re.Match[str], kind: str | None, text: str, source: str) -> dict[str, Any] | None:
    width = int(match.group("width")) * 100
    height = int(match.group("height")) * 100
    if not valid_opening_mark_size(width, height, kind):
        return None
    return {
        "kind": kind,
        "text": re.sub(r"\s+", "", text.upper()),
        "width_mm": width,
        "height_mm": height,
        "source": source,
    }


def valid_opening_mark_size(width: int, height: int, kind: str | None) -> bool:
    if not 300 <= width <= 6000:
        return False
    if kind == "door":
        return 1500 <= height <= 3600
    if kind == "window":
        return 300 <= height <= 3600
    return 300 <= height <= 3600
