from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent_memory import AgentMemory, build_retrieval_query
from .ai_client import generate_agent_reflection
from .excel_reader import read_design_notes
from .outputs import write_bim_outputs
from .pipeline import run_bim_pipeline


def run_bim_agent(
    excel_path: str | Path,
    notes_path: str | Path,
    out_dir: str | Path,
    *,
    memory_dir: str | Path = "agent_memory",
    retrieve_limit: int = 3,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> dict:
    """Run the memory-augmented BIM modeling agent.

    This is the agent layer above the fixed pipeline. It owns task state,
    retrieves prior experience, triggers LLM understanding/planning, reflects on
    the result, and stores the new case back into memory.
    """

    excel_path = Path(excel_path)
    notes_path = Path(notes_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    memory = AgentMemory(memory_dir)
    case_id = memory.create_case_id()
    notes_text = read_design_notes(notes_path)
    retrieval_query = build_retrieval_query(excel_path, notes_text)
    memory_context = memory.memory_context(retrieval_query, limit=retrieve_limit)

    agent_trace: dict[str, Any] = {
        "agent_name": "MemoryAugmentedBimModelingAgent",
        "case_id": case_id,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "state": "started",
        "steps": [
            _step("perceive_inputs", "completed", {"excel": str(excel_path), "notes": str(notes_path)}),
            _step("retrieve_memory", "completed", memory_context),
        ],
    }

    standard_model: dict | None = None
    execution_plan: dict | None = None
    reflection: dict | None = None
    ai_info: dict | None = None

    try:
        standard_model = run_bim_pipeline(
            excel_path,
            notes_path,
            out_dir,
            memory_context=memory_context,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        execution_plan = standard_model.get("llm_revit_execution_plan", {})
        agent_trace["steps"].append(
            _step(
                "llm_understand_and_plan",
                "completed",
                {
                    "component_counts": _component_counts(standard_model),
                    "operation_count": len(execution_plan.get("operations", [])) if isinstance(execution_plan, dict) else 0,
                },
            )
        )
        ai_info = _read_json(out_dir / "ai_call_info.json")

        reflection, reflection_ai_info = generate_agent_reflection(
            standard_model=standard_model,
            execution_plan=execution_plan,
            agent_trace=agent_trace,
            memory_context=memory_context,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        agent_trace["steps"].append(_step("reflect_and_extract_lessons", "completed", reflection))
        if ai_info is None:
            ai_info = {"used": True, "required": True, "stages": []}
        ai_info.setdefault("stages", []).append(reflection_ai_info)
        ai_info["agent_enabled"] = True
        ai_info["agent_case_id"] = case_id

        standard_model["agent"] = {
            "case_id": case_id,
            "memory_context": memory_context,
            "reflection": reflection,
        }
        write_bim_outputs(standard_model, out_dir, ai_info)

        agent_trace["state"] = "completed"
        agent_trace["completed_at"] = datetime.now().isoformat(timespec="seconds")
        _write_json(out_dir / "agent_trace.json", agent_trace)
        _write_json(out_dir / "agent_memory_context.json", memory_context)
        _write_json(out_dir / "lessons_learned.json", reflection)
        _copy_inputs(excel_path, notes_path, out_dir)

        case_dir = memory.save_case(
            case_id=case_id,
            excel_path=str(excel_path),
            notes_path=str(notes_path),
            output_dir=str(out_dir),
            standard_model=standard_model,
            execution_plan=execution_plan,
            ai_info=ai_info,
            agent_trace=agent_trace,
            reflection=reflection,
        )
        agent_trace["memory_case_path"] = str(case_dir)
        _write_json(out_dir / "agent_trace.json", agent_trace)
        return standard_model

    except RuntimeError as exc:
        agent_trace["state"] = "failed"
        agent_trace["failed_at"] = datetime.now().isoformat(timespec="seconds")
        agent_trace["error"] = str(exc)
        agent_trace["steps"].append(_step("agent_failed", "failed", {"error": str(exc)}))
        _write_json(out_dir / "agent_trace.json", agent_trace)
        _write_json(out_dir / "agent_memory_context.json", memory_context)
        memory.save_case(
            case_id=case_id,
            excel_path=str(excel_path),
            notes_path=str(notes_path),
            output_dir=str(out_dir),
            standard_model=standard_model,
            execution_plan=execution_plan,
            ai_info=ai_info,
            agent_trace=agent_trace,
            reflection=reflection,
        )
        raise


def _step(name: str, status: str, detail: dict) -> dict:
    return {
        "name": name,
        "status": status,
        "time": datetime.now().isoformat(timespec="seconds"),
        "detail": detail,
    }


def _component_counts(model: dict) -> dict:
    return {
        key: len(value)
        for key, value in model.get("components", {}).items()
        if isinstance(value, list)
    }


def _copy_inputs(excel_path: Path, notes_path: Path, out_dir: Path) -> None:
    inputs_dir = out_dir / "agent_inputs"
    inputs_dir.mkdir(exist_ok=True)
    if excel_path.exists():
        shutil.copy2(excel_path, inputs_dir / excel_path.name)
    if notes_path.exists():
        shutil.copy2(notes_path, inputs_dir / notes_path.name)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

