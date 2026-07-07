from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from .models import AgentOutputResult


def default_repair(raw_text: str) -> str:
    text = raw_text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?\s*```$", "", text)
        text = text.strip()

    brace_start = text.find("{")
    bracket_start = text.find("[")

    if brace_start >= 0 and (bracket_start < 0 or brace_start < bracket_start):
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            if depth == 0:
                return text[brace_start : i + 1]
    elif bracket_start >= 0:
        depth = 0
        for i in range(bracket_start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
            if depth == 0:
                return text[bracket_start : i + 1]

    return text


def validate_agent_output(
    raw_text: str,
    schema: type[BaseModel],
    repair_fn: Callable[[str], str] | None = None,
) -> AgentOutputResult:
    result = AgentOutputResult(
        status="valid",
        raw_text=raw_text,
    )

    try:
        parsed = schema.model_validate_json(raw_text)
        result.parsed = parsed.model_dump(mode="json")
        return result
    except Exception as e:
        error = str(e)

    if repair_fn:
        try:
            repaired = repair_fn(raw_text)
            parsed = schema.model_validate_json(repaired)
            result.parsed = parsed.model_dump(mode="json")
            result.status = "repaired"
            return result
        except Exception as e:
            error = str(e)

    result.status = "failed"
    result.error = error
    return result


def validate_planner_output(
    raw_text: str,
) -> tuple[AgentOutputResult, dict[str, Any] | None]:
    from .models import PlanResult, WorkerQuestion

    result = validate_agent_output(raw_text, PlanResult, repair_fn=default_repair)
    if result.status != "failed":
        return result, result.parsed

    result2 = validate_agent_output(raw_text, WorkerQuestion, repair_fn=default_repair)
    if result2.status != "failed":
        return result2, result2.parsed

    return result, None
