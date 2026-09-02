from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from .dxf_parser import CadEntity


GENERAL_NOTE_KEYWORDS = [
    "\u65bd\u5de5\u8bbe\u8ba1\u603b\u8bf4\u660e",
    "\u7ed3\u6784\u8bbe\u8ba1\u603b\u8bf4\u660e",
    "\u5efa\u7b51\u8bbe\u8ba1\u8bf4\u660e",
    "\u8bbe\u8ba1\u603b\u8bf4\u660e",
    "\u603b\u8bf4\u660e",
    "\u8bbe\u8ba1\u4f9d\u636e",
    "\u5de5\u7a0b\u6982\u51b5",
    "\u65bd\u5de5\u8981\u6c42",
    "\u6750\u6599",
    "\u6297\u9707",
    "\u8010\u706b",
    "\u9632\u706b",
    "\u9632\u6c34",
    "\u6df7\u51dd\u571f",
    "\u780c\u4f53",
    "\u94a2\u7b4b",
    "\u4fdd\u6e29",
    "\u8282\u80fd",
    "general notes",
    "design notes",
    "construction notes",
    "specification",
]

PLAN_KEYWORDS = [
    "\u5efa\u7b51\u5e73\u9762\u56fe",
    "\u5e73\u9762\u56fe",
    "\u9996\u5c42",
    "\u4e00\u5c42",
    "\u4e8c\u5c42",
    "\u4e09\u5c42",
    "\u6807\u51c6\u5c42",
    "\u5c4b\u9876\u5c42",
    "\u8f74\u7ebf",
    "floor plan",
    "architectural plan",
]

ELEVATION_KEYWORDS = [
    "\u5efa\u7b51\u7acb\u9762\u56fe",
    "\u7acb\u9762\u56fe",
    "\u6b63\u7acb\u9762",
    "\u80cc\u7acb\u9762",
    "\u5357\u7acb\u9762",
    "\u5317\u7acb\u9762",
    "\u4e1c\u7acb\u9762",
    "\u897f\u7acb\u9762",
    "\u5de6\u7acb\u9762",
    "\u53f3\u7acb\u9762",
    "elevation",
]

DETAIL_KEYWORDS = [
    "\u5efa\u7b51\u8be6\u56fe",
    "\u8be6\u56fe",
    "\u8282\u70b9\u8be6\u56fe",
    "\u5927\u6837\u56fe",
    "\u5899\u8eab\u5927\u6837",
    "\u697c\u68af\u8be6\u56fe",
    "\u95e8\u7a97\u8be6\u56fe",
    "\u6784\u9020\u8be6\u56fe",
    "\u8282\u70b9",
    "detail",
    "details",
]

SECTION_KEYWORDS = [
    "\u5efa\u7b51\u5256\u9762\u56fe",
    "\u5256\u9762\u56fe",
    "\u5256\u9762",
    "section",
]

ASSEMBLY_KEYWORDS = [
    "\u603b\u88c5\u56fe",
    "\u603b\u5e73\u9762\u56fe",
    "\u603b\u56fe",
    "\u603b\u88c5",
    "\u603b\u5e03\u7f6e",
    "assembly",
    "general arrangement",
    "ga drawing",
]

DRAWING_KEYWORDS = {
    "general_notes": GENERAL_NOTE_KEYWORDS,
    "architectural_plan": PLAN_KEYWORDS,
    "architectural_elevation": ELEVATION_KEYWORDS,
    "architectural_detail": DETAIL_KEYWORDS,
    "architectural_section": SECTION_KEYWORDS,
    "general_assembly": ASSEMBLY_KEYWORDS,
}

DISPLAY_TYPE_NAMES = {
    "general_notes": "\u65bd\u5de5/\u8bbe\u8ba1\u603b\u8bf4\u660e",
    "architectural_plan": "\u5efa\u7b51\u5e73\u9762\u56fe",
    "architectural_elevation": "\u5efa\u7b51\u7acb\u9762\u56fe",
    "architectural_detail": "\u5efa\u7b51\u8be6\u56fe",
    "architectural_section": "\u5efa\u7b51\u5256\u9762\u56fe",
    "general_assembly": "\u603b\u88c5/\u603b\u5e73/\u603b\u5e03\u7f6e\u56fe",
    "mixed_or_unknown": "\u6df7\u5408\u6216\u672a\u77e5",
}


@dataclass
class TextItem:
    text: str
    layer: str
    point: tuple[float, float]
    height: float | None
    rotation: float | None


def analyze_text_and_notes(entities: list[CadEntity], object_counts: dict) -> dict:
    text_items = extract_text_items(entities)
    full_text = "\n".join(item.text for item in text_items if item.text.strip())
    title_info = detect_drawing_title(text_items)
    scoring_text = "\n".join([title_info["title"] or "", full_text])
    scores = {name: score_keywords(scoring_text, keywords) for name, keywords in DRAWING_KEYWORDS.items()}
    general_score = scores["general_notes"]
    plan_score = scores["architectural_plan"]

    geometry_score = (
        int(object_counts.get("walls", 0)) * 3
        + int(object_counts.get("openings", 0)) * 2
        + int(object_counts.get("axes", 0)) * 2
    )
    text_char_count = len(full_text.replace("\n", ""))

    drawing_type = classify_drawing(scores, geometry_score, text_char_count)
    structured = extract_basic_fields(full_text)

    return {
        "drawing_type": drawing_type,
        "drawing_type_name": DISPLAY_TYPE_NAMES.get(drawing_type, drawing_type),
        "drawing_title": title_info["title"],
        "drawing_title_confidence": title_info["confidence"],
        "drawing_title_candidates": title_info["candidates"],
        "source": "cad_text",
        "text_count": len(text_items),
        "text_char_count": text_char_count,
        "general_note_score": general_score,
        "plan_score": plan_score,
        "drawing_type_scores": scores,
        "geometry_score": geometry_score,
        "full_text": full_text,
        "text_items": [asdict(item) for item in text_items],
        "structured": structured,
    }


def extract_text_items(entities: list[CadEntity]) -> list[TextItem]:
    items: list[TextItem] = []
    for ent in entities:
        if ent.type not in {"TEXT", "MTEXT"}:
            continue
        text = clean_cad_text(str(ent.data.get("text", "")))
        if not text:
            continue
        items.append(
            TextItem(
                text=text,
                layer=ent.layer,
                point=tuple(ent.data.get("point", (0.0, 0.0))),
                height=ent.data.get("height"),
                rotation=ent.data.get("rotation"),
            )
        )
    items.sort(key=lambda item: (-item.point[1], item.point[0]))
    return items


def detect_drawing_title(text_items: list[TextItem]) -> dict:
    candidates: list[dict] = []
    for item in text_items:
        text = item.text.strip()
        if not text:
            continue
        title_text = extract_title_text(item)
        score = drawing_title_score(item, title_text)
        if score <= 0:
            continue
        candidates.append(
            {
                "text": title_text,
                "raw_text": text,
                "score": score,
                "layer": item.layer,
                "point": item.point,
                "height": item.height,
            }
        )

    candidates.sort(key=lambda c: (-float(c["score"]), -float(c.get("height") or 0), -float(c["point"][1])))
    if not candidates:
        return {"title": None, "confidence": 0.0, "candidates": []}

    best = candidates[0]
    confidence = min(0.98, 0.45 + float(best["score"]) / 20)
    return {
        "title": best["text"],
        "confidence": round(confidence, 3),
        "candidates": candidates[:5],
    }


def extract_title_text(item: TextItem) -> str:
    text = item.text.strip()
    directional = extract_directional_elevation_title(text)
    if directional:
        return directional
    explicit = re.search(r"(\u56fe\u540d|\u56fe\u7eb8\u540d\u79f0|drawing\s*(title|name))\s*[:：]\s*(.+)", text, re.I)
    if explicit:
        return explicit.group(3).strip()[:80]

    # Preserve the level qualifier so English titles can drive level inference.
    first_part = re.split(r"[\n\u3002\uff1b;]", text)[0].strip()
    english_plan = re.search(
        r"\b(?:(?:lower\s+ground|ground|basement|mezzanine|roof|typical|"
        r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
        r"\d+(?:st|nd|rd|th)?)\s+)?floor\s+plan\b",
        first_part,
        re.I,
    )
    if english_plan:
        return re.sub(r"\s+", " ", english_plan.group(0)).strip()[:80]

    known_titles = [
        "\u65bd\u5de5\u8bbe\u8ba1\u603b\u8bf4\u660e",
        "\u7ed3\u6784\u8bbe\u8ba1\u603b\u8bf4\u660e",
        "\u5efa\u7b51\u8bbe\u8ba1\u8bf4\u660e",
        "\u5efa\u7b51\u5e73\u9762\u56fe",
        "\u5efa\u7b51\u7acb\u9762\u56fe",
        "\u5efa\u7b51\u5256\u9762\u56fe",
        "\u5efa\u7b51\u8be6\u56fe",
        "\u603b\u5e73\u9762\u56fe",
        "\u603b\u88c5\u56fe",
        "\u5899\u8eab\u5927\u6837",
        "\u8282\u70b9\u8be6\u56fe",
        "\u5927\u6837\u56fe",
        "\u5e73\u9762\u56fe",
        "\u7acb\u9762\u56fe",
        "\u5256\u9762\u56fe",
        "\u8be6\u56fe",
        "\u603b\u8bf4\u660e",
        "\u603b\u88c5",
        "\u603b\u5e03\u7f6e",
        "general notes",
        "floor plan",
        "elevation",
        "section",
        "detail",
        "assembly",
        "general arrangement",
    ]
    low = text.lower()
    for title in known_titles:
        if title.lower() in low:
            return title

    first_part = re.split(r"[\n。；;]", text)[0].strip()
    return first_part[:80]


def extract_directional_elevation_title(text: str) -> str | None:
    first_part = re.split(r"[\n\u3002\uff1b;]", text.strip())[0].strip()
    patterns = [
        r"((?:\u4e1c|\u897f|\u5357|\u5317|\u6b63|\u80cc|\u5de6|\u53f3)\s*(?:\u7acb\u9762\u56fe|\u7acb\u9762|\u9762\u56fe|\u9762))",
        r"([A-Za-z0-9]+[-~]?[A-Za-z0-9]*\s*(?:\u7acb\u9762\u56fe|\u7acb\u9762|\u9762\u56fe|\u9762))",
        r"((?:north|south|east|west)[^\n;]{0,24}(?:elevation|facade))",
    ]
    for pattern in patterns:
        match = re.search(pattern, first_part, re.I)
        if match:
            value = match.group(1).strip()
            if re.search(r"[\u4e00-\u9fa5]", value):
                return re.sub(r"\s+", "", value)[:80]
            return re.sub(r"\s+", " ", value)[:80]
    return None


def drawing_title_score(item: TextItem, title_text: str) -> float:
    text = item.text.strip()
    low = title_text.lower()
    layer_low = item.layer.lower()
    if len(title_text) < 2 or len(title_text) > 80:
        return 0.0

    score = 0.0
    has_title_signal = False
    title_keywords = [
        "\u56fe\u540d",
        "\u56fe\u7eb8\u540d\u79f0",
        "\u5de5\u7a0b\u540d\u79f0",
        "\u5efa\u7b51\u5e73\u9762\u56fe",
        "\u5e73\u9762\u56fe",
        "\u7acb\u9762\u56fe",
        "\u8be6\u56fe",
        "\u5927\u6837\u56fe",
        "\u5256\u9762\u56fe",
        "\u603b\u8bf4\u660e",
        "\u65bd\u5de5\u8bbe\u8ba1\u603b\u8bf4\u660e",
        "\u603b\u88c5\u56fe",
        "\u603b\u5e73\u9762\u56fe",
        "\u603b\u5e03\u7f6e",
        "drawing title",
        "drawing name",
        "floor plan",
        "elevation",
        "detail",
        "section",
        "general notes",
        "assembly",
    ]
    for keyword in title_keywords:
        if keyword.lower() in low:
            score += 6 if len(keyword) >= 4 else 3
            has_title_signal = True

    if re.search(r"(title|name|\u56fe\u540d|\u56fe\u7b7e|\u6807\u9898)", layer_low, re.I):
        score += 4
        has_title_signal = True

    if re.search(r"(^|\s)[A-Z]?\d+[-~]?[A-Z]?\d*\s*(\u5c42|\u5e73\u9762\u56fe)", title_text):
        score += 4
        has_title_signal = True
    if item.height and item.height >= 200:
        score += min(4, item.height / 150)
    if re.search(r"^\s*(\u56fe\u540d|\u56fe\u7eb8\u540d\u79f0)\s*[:：]", text):
        score += 8
        has_title_signal = True
    if re.search(r"^\s*(\u8bbe\u8ba1|\u6821\u5bf9|\u5ba1\u6838|\u5ba1\u5b9a|date|scale|project)\s*[:：]?", low):
        score -= 6
    if re.search(r"(slope|1[:/]\d+|%|\u8010\u706b\u7b49\u7ea7|\u6750\u6599|\u6807\u9ad8)", low, re.I):
        score -= 8
    if len(title_text) <= 4 and not re.search(r"[\u56fe\u8bf4\u660e]", title_text):
        score -= 3
    return score if has_title_signal else 0.0


def clean_cad_text(text: str) -> str:
    text = text.replace("\\P", "\n")
    text = re.sub(r"\\[A-Za-z]+[0-9.+-]*;?", "", text)
    text = re.sub(r"\|[^;\n]{1,120};", "", text)
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def score_keywords(text: str, keywords: list[str]) -> int:
    low = text.lower()
    score = 0
    for keyword in keywords:
        count = low.count(keyword.lower())
        if count:
            score += count * (3 if len(keyword) >= 4 else 1)
    return score


def classify_drawing(scores: dict[str, int], geometry_score: int, text_char_count: int) -> str:
    general_score = scores.get("general_notes", 0)
    plan_score = scores.get("architectural_plan", 0)

    if general_score >= 6 and text_char_count >= 40 and general_score >= plan_score:
        return "general_notes"
    if text_char_count >= 80 and general_score >= 3:
        return "general_notes"

    non_general = {
        key: value
        for key, value in scores.items()
        if key != "general_notes"
    }
    best_type = max(non_general, key=non_general.get)
    best_score = non_general[best_type]

    if best_type == "architectural_elevation" and best_score >= 2:
        return best_type
    if best_score >= 3:
        return best_type
    if geometry_score >= 6 and geometry_score >= general_score:
        return "architectural_plan"
    if plan_score >= 2 and geometry_score >= 2:
        return "architectural_plan"
    return "mixed_or_unknown"


def extract_basic_fields(text: str) -> dict:
    fields = {
        "design_life": find_first(text, [r"(\d+)\s*\u5e74\u8bbe\u8ba1\u4f7f\u7528\u5e74\u9650", r"\u8bbe\u8ba1\u4f7f\u7528\u5e74\u9650[^\d]*(\d+)\s*\u5e74"]),
        "seismic_intensity": find_first(text, [r"\u6297\u9707[^\d]*(\d+)\s*\u5ea6", r"\u8bbe\u9632\u70c8\u5ea6[^\d]*(\d+)\s*\u5ea6"]),
        "fire_rating": find_first(text, [r"\u8010\u706b\u7b49\u7ea7[^\u4e00-\u9fa5A-Za-z0-9]*([\u4e00-\u9fa5A-Za-z0-9]+)"]),
        "concrete_grades": sorted(set(re.findall(r"C\d{2,3}", text, flags=re.I))),
        "mentions_waterproofing": bool(re.search("\u9632\u6c34", text)),
        "mentions_insulation": bool(re.search("\u4fdd\u6e29|\u8282\u80fd", text)),
        "mentions_masonry": bool(re.search("\u780c\u4f53|\u780c\u5757|\u52a0\u6c14\u6df7\u51dd\u571f", text)),
    }
    return fields


def find_first(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1)
    return None
