from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from .geometry import distance
from .notes import TextItem, extract_text_items
from .dxf_parser import CadEntity


FACE_LABEL_RE = re.compile(
    r"(^|[\s\-_:：])(?P<label>([A-Za-z0-9]+|[\u4e00-\u9fa5]{1,4})\s*(面|立面|facade|elevation))($|[\s\-_:：])",
    re.I,
)

DIRECTION_ELEVATION_RE = re.compile(r"(东|西|南|北|正|背|左|右).{0,4}立面")


@dataclass
class ObjectRelation:
    object_id: str
    object_kind: str
    relation: str
    context_name: str
    context_source: str
    drawing_title: str | None
    drawing_type: str | None
    frame_id: str | None
    host_wall_id: str | None
    point: tuple[float, float] | None
    confidence: float
    human_readable: str


@dataclass
class ContextSummary:
    context_name: str
    context_source: str
    drawing_title: str | None
    drawing_type: str | None
    frame_id: str | None
    doors: int
    windows: int
    openings_total: int
    object_ids: str
    human_readable: str


def analyze_relations(entities: list[CadEntity], result: dict) -> dict:
    text_items = extract_text_items(entities)
    openings = result.get("openings", [])
    relations = [opening_relation(opening, text_items, result) for opening in openings]
    summaries = summarize_contexts(relations)
    return {
        "object_relations": [asdict(item) for item in relations],
        "context_summaries": [asdict(item) for item in summaries],
    }


def opening_relation(opening: dict, text_items: list[TextItem], result: dict) -> ObjectRelation:
    point = tuple(opening.get("point", (0.0, 0.0)))
    context_name, context_source, context_confidence = infer_context(point, text_items, result)
    kind = str(opening.get("kind", "opening"))
    object_id = str(opening.get("id", ""))
    readable_kind = {"door": "门", "window": "窗"}.get(kind, kind)
    human = f"{context_name} 上有 1 个{readable_kind}（{object_id}）"
    return ObjectRelation(
        object_id=object_id,
        object_kind=kind,
        relation="located_on_context",
        context_name=context_name,
        context_source=context_source,
        drawing_title=result.get("notes", {}).get("drawing_title"),
        drawing_type=result.get("notes", {}).get("drawing_type"),
        frame_id=result.get("frame", {}).get("id"),
        host_wall_id=opening.get("host_wall_id"),
        point=point,
        confidence=context_confidence,
        human_readable=human,
    )


def infer_context(point: tuple[float, float], text_items: list[TextItem], result: dict) -> tuple[str, str, float]:
    nearest = nearest_face_label(point, text_items)
    if nearest:
        label, dist = nearest
        confidence = 0.88 if dist < 2500 else 0.72
        return label, "nearest_face_label", confidence

    title = result.get("notes", {}).get("drawing_title")
    if title:
        face = extract_face_label(title)
        if face:
            return face, "drawing_title_face", 0.82
        return str(title), "drawing_title", 0.66

    frame_id = result.get("frame", {}).get("id")
    if frame_id:
        return str(frame_id), "drawing_frame", 0.55

    return "未命名图纸范围", "fallback", 0.35


def nearest_face_label(point: tuple[float, float], text_items: list[TextItem], max_distance: float = 8000) -> tuple[str, float] | None:
    best_label = None
    best_dist = max_distance
    for item in text_items:
        label = extract_face_label(item.text)
        if not label:
            continue
        d = distance(point, item.point)
        if d < best_dist:
            best_label = label
            best_dist = d
    if best_label is None:
        return None
    return best_label, best_dist


def extract_face_label(text: str | None) -> str | None:
    if not text:
        return None
    text = str(text).strip()
    direction = DIRECTION_ELEVATION_RE.search(text)
    if direction:
        return direction.group(0)
    match = FACE_LABEL_RE.search(f" {text} ")
    if match:
        return re.sub(r"\s+", "", match.group("label"))
    if re.fullmatch(r"[A-Za-z0-9]\s*面", text, re.I):
        return re.sub(r"\s+", "", text)
    return None


def summarize_contexts(relations: list[ObjectRelation]) -> list[ContextSummary]:
    grouped: dict[tuple[str, str, str | None, str | None, str | None], list[ObjectRelation]] = {}
    for relation in relations:
        key = (
            relation.context_name,
            relation.context_source,
            relation.drawing_title,
            relation.drawing_type,
            relation.frame_id,
        )
        grouped.setdefault(key, []).append(relation)

    summaries: list[ContextSummary] = []
    for (context_name, context_source, drawing_title, drawing_type, frame_id), items in grouped.items():
        doors = sum(1 for item in items if item.object_kind == "door")
        windows = sum(1 for item in items if item.object_kind == "window")
        parts = []
        if doors:
            parts.append(f"{doors} 个门")
        if windows:
            parts.append(f"{windows} 个窗")
        readable = f"{context_name} 上有 " + ("、".join(parts) if parts else f"{len(items)} 个洞口")
        summaries.append(
            ContextSummary(
                context_name=context_name,
                context_source=context_source,
                drawing_title=drawing_title,
                drawing_type=drawing_type,
                frame_id=frame_id,
                doors=doors,
                windows=windows,
                openings_total=len(items),
                object_ids=", ".join(item.object_id for item in items),
                human_readable=readable,
            )
        )
    return summaries
