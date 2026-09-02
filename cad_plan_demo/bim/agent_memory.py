from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_MEMORY_DIR = "agent_memory"


class AgentMemory:
    """File-based case memory for the BIM modeling agent.

    The first version deliberately uses transparent JSON files instead of a
    database. This makes the agent's "experience" easy to inspect in a
    dissertation demo.
    """

    def __init__(self, root: str | Path = DEFAULT_MEMORY_DIR) -> None:
        self.root = Path(root)
        self.cases_dir = self.root / "cases"
        self.index_path = self.root / "memory_index.json"
        self.cases_dir.mkdir(parents=True, exist_ok=True)

    def create_case_id(self) -> str:
        return datetime.now().strftime("case_%Y%m%d_%H%M%S")

    def retrieve(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return []

        scored: list[tuple[int, dict[str, Any]]] = []
        for item in self._load_index():
            text = " ".join(
                [
                    str(item.get("excel", "")),
                    str(item.get("notes_summary", "")),
                    str(item.get("lessons_summary", "")),
                    str(item.get("failure_summary", "")),
                ]
            )
            score = len(query_tokens & _tokens(text))
            if score > 0:
                scored.append((score, item))

        scored.sort(key=lambda pair: (pair[0], pair[1].get("created_at", "")), reverse=True)
        return [item for _, item in scored[:limit]]

    def memory_context(self, query: str, limit: int = 3) -> dict[str, Any]:
        cases = self.retrieve(query, limit=limit)
        return {
            "retrieval_method": "keyword_overlap_case_memory",
            "case_count": len(cases),
            "cases": [
                {
                    "case_id": item.get("case_id"),
                    "notes_summary": item.get("notes_summary", ""),
                    "lessons_summary": item.get("lessons_summary", ""),
                    "failure_summary": item.get("failure_summary", ""),
                    "case_path": item.get("case_path", ""),
                }
                for item in cases
            ],
        }

    def save_case(
        self,
        *,
        case_id: str,
        excel_path: str,
        notes_path: str,
        output_dir: str,
        standard_model: dict | None,
        execution_plan: dict | None,
        ai_info: dict | None,
        agent_trace: dict,
        reflection: dict | None = None,
    ) -> Path:
        case_dir = self.cases_dir / case_id
        case_dir.mkdir(parents=True, exist_ok=True)

        summary = _case_summary(
            case_id=case_id,
            excel_path=excel_path,
            notes_path=notes_path,
            output_dir=output_dir,
            standard_model=standard_model,
            execution_plan=execution_plan,
            reflection=reflection,
        )
        _write_json(case_dir / "case_summary.json", summary)
        _write_json(case_dir / "standard_model.json", standard_model or {})
        _write_json(case_dir / "llm_revit_execution_plan.json", execution_plan or {})
        _write_json(case_dir / "ai_call_info.json", ai_info or {})
        _write_json(case_dir / "agent_trace.json", agent_trace)
        if reflection is not None:
            _write_json(case_dir / "lessons_learned.json", reflection)

        self._upsert_index(summary | {"case_path": str(case_dir)})
        return case_dir

    def save_reflection(self, case_id: str, reflection: dict) -> None:
        case_dir = self.cases_dir / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        _write_json(case_dir / "lessons_learned.json", reflection)

        index = self._load_index()
        for item in index:
            if item.get("case_id") == case_id:
                item["lessons_summary"] = _lessons_summary(reflection)
                break
        _write_json(self.index_path, index)

    def _load_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _upsert_index(self, summary: dict[str, Any]) -> None:
        index = [item for item in self._load_index() if item.get("case_id") != summary.get("case_id")]
        index.append(summary)
        _write_json(self.index_path, index)


def build_retrieval_query(excel_path: str | Path, notes_text: str) -> str:
    compact_notes = " ".join(notes_text.split())[:1200]
    return f"{Path(excel_path).name} {compact_notes}"


def _case_summary(
    *,
    case_id: str,
    excel_path: str,
    notes_path: str,
    output_dir: str,
    standard_model: dict | None,
    execution_plan: dict | None,
    reflection: dict | None,
) -> dict[str, Any]:
    components = (standard_model or {}).get("components", {})
    validation = (standard_model or {}).get("validation", {})
    issues = validation.get("issues", []) if isinstance(validation, dict) else []
    plan_operations = (execution_plan or {}).get("operations", [])
    return {
        "case_id": case_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "excel": excel_path,
        "notes": notes_path,
        "output_dir": output_dir,
        "component_counts": {key: len(value) for key, value in components.items() if isinstance(value, list)},
        "operation_count": len(plan_operations) if isinstance(plan_operations, list) else 0,
        "requires_human_confirmation": bool(validation.get("requires_human_confirmation", False)) if isinstance(validation, dict) else False,
        "failure_summary": _issue_summary(issues),
        "lessons_summary": _lessons_summary(reflection or {}),
        "notes_summary": "",
    }


def _issue_summary(issues: list[dict]) -> str:
    if not issues:
        return "No validation issues."
    parts = []
    for issue in issues[:8]:
        parts.append(
            "{group}:{component} {message}".format(
                group=issue.get("component_group", ""),
                component=issue.get("component_id", ""),
                message=issue.get("message", ""),
            )
        )
    return " | ".join(parts)


def _lessons_summary(reflection: dict) -> str:
    if not reflection:
        return ""
    lessons = reflection.get("lessons", [])
    if isinstance(lessons, list):
        return " | ".join(str(item.get("future_rule", item)) for item in lessons[:5])
    return str(reflection)[:800]


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[\w\u4e00-\u9fff]+", text) if len(token) >= 2}


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

