from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .schema import MODEL_SEQUENCE, schema_copy


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_MAX_TOKENS = 32000

DOOR_FAMILY_GLOSSARY = {
    "Flush": "Flush Panel; 平板门、无明显凹凸装饰的门",
    "Glazed": "Glazed Door; 带玻璃门",
    "Vision": "Vision Panel; 带观察窗",
    "Lite": "Lite; 带观察窗",
    "Louvered": "Louvered Door; 百叶门",
    "w Side Panel": "With Side Panel; 带侧窗或侧面固定扇",
    "Transom": "Transom; 带上亮窗",
    "Dbl Acting": "Double Acting; 双向开启门",
    "Sliding": "Sliding Door; 推拉门",
    "Pocket": "Pocket Door; 暗藏式推拉门，门扇滑进墙内",
    "BiFold": "Bi-fold Door; 折叠门",
    "Overhead": "Overhead Door; 上翻式车库门",
    "Rolling": "Rolling Door; 卷帘门",
    "Garage": "Garage Door; 车库门",
    "Opening": "Opening; 只有门洞，没有门扇",
    "Cased Opening": "Cased Opening; 带门套但没有门扇的洞口",
}


def standardize_with_llm(
    normalized_model: dict,
    notes: str,
    *,
    memory_context: dict | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[dict | None, dict]:
    """Ask an OpenAI-compatible model to create the BIM component JSON."""

    key = api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        return None, {"used": False, "reason": "No DEEPSEEK_API_KEY or OPENAI_API_KEY configured."}

    url = (base_url or os.getenv("BIM_LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
    selected_model = model or os.getenv("BIM_LLM_MODEL") or DEFAULT_MODEL
    messages = [
            {
                "role": "system",
                "content": (
                    "You convert CAD-derived Excel data and design notes into a fixed BIM JSON schema. "
                    "You are the required semantic understanding step in this workflow. "
                    "Do not invent missing, conflicting, or uncertain values. "
                    "Mark uncertain components as review_status=needs_review and add validation issues. "
                    "Return JSON only."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "schema": schema_copy(),
                        "normalized_input": normalized_model,
                        "design_notes": notes[:12000],
                        "door_family_glossary": DOOR_FAMILY_GLOSSARY,
                        "retrieved_agent_memory": memory_context or {},
                    },
                    ensure_ascii=False,
                ),
            },
    ]
    payload = {
        "model": selected_model,
        "temperature": 0.1,
        "messages": messages,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "max_tokens": DEFAULT_MAX_TOKENS,
    }

    try:
        response_data = _post_chat_completion(url, key, payload, timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, {"used": False, "reason": f"LLM request failed: {exc}"}

    content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _parse_json_content(content)
    repair_info = {"attempted": False, "succeeded": False}
    if parsed is None:
        parsed, repair_info = _repair_json_response(
            url=url,
            key=key,
            selected_model=selected_model,
            original_messages=messages,
            bad_content=content,
            timeout=timeout,
            purpose="standard BIM component JSON",
        )
    if parsed is None:
        reason = "LLM response did not contain valid JSON."
        if repair_info["attempted"]:
            reason += " Automatic JSON repair also failed."
        return None, {"used": False, "reason": reason, "json_repair": repair_info}
    return parsed, {"used": True, "base_url": url, "model": selected_model, "json_repair": repair_info}


def require_standard_model_from_llm(
    normalized_model: dict,
    notes: str,
    *,
    memory_context: dict | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> tuple[dict, dict]:
    ai_model, ai_info = standardize_with_llm(
        normalized_model,
        notes,
        memory_context=memory_context,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
    if ai_model is None:
        reason = ai_info.get("reason", "LLM standardization failed.")
        raise RuntimeError(f"DeepSeek semantic understanding is required, but it failed: {reason}")
    ai_info["stage"] = "excel_notes_to_standard_components"
    return ai_model, ai_info


def generate_revit_execution_plan(
    standard_model: dict,
    *,
    memory_context: dict | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[dict, dict]:
    """Ask the model to translate components into a Revit API operation plan."""

    key = api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("DeepSeek Revit planning is required, but no DEEPSEEK_API_KEY or OPENAI_API_KEY is configured.")

    url = (base_url or os.getenv("BIM_LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
    selected_model = model or os.getenv("BIM_LLM_MODEL") or DEFAULT_MODEL
    plan_contract = {
        "schema_version": "1.0",
        "purpose": "LLM-authored Revit API modeling plan",
        "allowed_operations": [
            "create_level",
            "create_grid",
            "create_structural_column",
            "create_straight_wall",
            "create_floor_slab",
            "create_floor_opening",
            "create_stair",
            "create_door",
            "create_window",
            "skip_with_reason",
        ],
        "required_top_level_keys": ["schema_version", "model_sequence", "operations", "planning_notes"],
        "operation_required_keys": ["operation_id", "operation", "component_group", "component_id", "parameters", "requires_human_confirmation", "reason"],
        "model_sequence": list(MODEL_SEQUENCE),
        "rule": "Do not create unsupported or uncertain elements. Use skip_with_reason when data is missing or review_status is needs_review/rejected.",
    }
    messages = [
            {
                "role": "system",
                "content": (
                    "You are the required Revit API planning agent. "
                    "Convert the standardized BIM component list into a strict JSON execution plan. "
                    "Do not write C# code. Do not call Revit directly. "
                    "Only use the allowed operations. Return JSON only."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "plan_contract": plan_contract,
                        "standard_model": standard_model,
                        "door_family_glossary": DOOR_FAMILY_GLOSSARY,
                        "retrieved_agent_memory": memory_context or {},
                    },
                    ensure_ascii=False,
                ),
            },
    ]
    payload = {
        "model": selected_model,
        "temperature": 0.1,
        "messages": messages,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "max_tokens": DEFAULT_MAX_TOKENS,
    }

    try:
        response_data = _post_chat_completion(url, key, payload, timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"DeepSeek Revit planning is required, but the request failed: {exc}") from exc

    content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _parse_json_content(content)
    repair_info = {"attempted": False, "succeeded": False}
    if parsed is None:
        parsed, repair_info = _repair_json_response(
            url=url,
            key=key,
            selected_model=selected_model,
            original_messages=messages,
            bad_content=content,
            timeout=timeout,
            purpose="Revit execution plan JSON",
        )
    if parsed is None:
        raise RuntimeError("DeepSeek Revit planning is required, but the response did not contain valid JSON. Automatic JSON repair also failed.")

    _normalize_plan(parsed)
    return parsed, {"used": True, "stage": "standard_components_to_revit_execution_plan", "base_url": url, "model": selected_model, "json_repair": repair_info}


def generate_agent_reflection(
    *,
    standard_model: dict,
    execution_plan: dict,
    agent_trace: dict,
    memory_context: dict | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[dict, dict]:
    key = api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("DeepSeek reflection is required, but no DEEPSEEK_API_KEY or OPENAI_API_KEY is configured.")

    url = (base_url or os.getenv("BIM_LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
    selected_model = model or os.getenv("BIM_LLM_MODEL") or DEFAULT_MODEL
    reflection_contract = {
        "schema_version": "1.0",
        "required_keys": ["case_quality", "lessons", "future_retrieval_tags", "human_confirmation_recommendations"],
        "lesson_keys": ["problem", "cause", "future_rule", "confidence"],
        "purpose": "Save reusable agent experience for future BIM modeling tasks.",
    }
    messages = [
            {
                "role": "system",
                "content": (
                    "You are the reflection module of a BIM modeling agent. "
                    "Analyze the current task, validation issues, skipped items, and planning decisions. "
                    "Extract reusable lessons for future tasks. Return JSON only."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "reflection_contract": reflection_contract,
                        "standard_model": standard_model,
                        "execution_plan": execution_plan,
                        "agent_trace": agent_trace,
                        "retrieved_agent_memory": memory_context or {},
                    },
                    ensure_ascii=False,
                ),
            },
    ]
    payload = {
        "model": selected_model,
        "temperature": 0.1,
        "messages": messages,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "max_tokens": DEFAULT_MAX_TOKENS,
    }
    try:
        response_data = _post_chat_completion(url, key, payload, timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"DeepSeek reflection is required, but the request failed: {exc}") from exc

    content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _parse_json_content(content)
    repair_info = {"attempted": False, "succeeded": False}
    if parsed is None:
        parsed, repair_info = _repair_json_response(
            url=url,
            key=key,
            selected_model=selected_model,
            original_messages=messages,
            bad_content=content,
            timeout=timeout,
            purpose="agent reflection JSON",
        )
    if parsed is None:
        raise RuntimeError("DeepSeek reflection is required, but the response did not contain valid JSON. Automatic JSON repair also failed.")
    parsed.setdefault("schema_version", "1.0")
    parsed.setdefault("lessons", [])
    parsed.setdefault("future_retrieval_tags", [])
    return parsed, {"used": True, "stage": "agent_reflection_and_memory_update", "base_url": url, "model": selected_model, "json_repair": repair_info}


def _normalize_plan(plan: dict) -> None:
    plan.setdefault("schema_version", "1.0")
    plan.setdefault("model_sequence", list(MODEL_SEQUENCE))
    plan.setdefault("operations", [])
    plan.setdefault("planning_notes", "")


def _post_chat_completion(url: str, key: str, payload: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _repair_json_response(
    *,
    url: str,
    key: str,
    selected_model: str,
    original_messages: list[dict],
    bad_content: str,
    timeout: int,
    purpose: str,
) -> tuple[dict | None, dict]:
    repair_info = {
        "attempted": True,
        "succeeded": False,
        "method": "llm_retry_with_previous_response",
    }
    repair_messages = [
        *original_messages,
        {"role": "assistant", "content": bad_content[:60000]},
        {
            "role": "user",
            "content": (
                f"Your previous response was not valid JSON for {purpose}. "
                "Return one complete valid JSON object only. "
                "Do not include markdown fences, explanations, comments, or trailing text. "
                "If any data is missing or uncertain, keep the value empty/null and mark it needs_review rather than inventing it."
            ),
        },
    ]
    payload = {
        "model": selected_model,
        "temperature": 0.0,
        "messages": repair_messages,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "max_tokens": DEFAULT_MAX_TOKENS,
    }
    try:
        response_data = _post_chat_completion(url, key, payload, timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        repair_info["reason"] = f"repair request failed: {exc}"
        return None, repair_info

    content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _parse_json_content(content)
    repair_info["succeeded"] = parsed is not None
    if parsed is None:
        repair_info["reason"] = "repair response still was not valid JSON"
    return parsed, repair_info


def _parse_json_content(content: str) -> dict | None:
    text = content.strip()
    candidates = [text]
    candidates.extend(match.strip() for match in re.findall(r"```(?:json)?\s*(.*?)```", text, re.S | re.I))
    extracted = _extract_json_object(text)
    if extracted:
        candidates.append(extracted)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1].strip()
    return None
