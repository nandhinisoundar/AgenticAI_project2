"""Assignment-compliant LLM reviewer with an explicit oracle-free view."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable

from gate_and_experiments.contracts import Decision

MAX_OUTPUT_CHARS = 1500
PROMPT_VERSION = "v2"
PROMPT_PATH = Path(__file__).parent / "prompts" / f"reviewer_{PROMPT_VERSION}.txt"
PROMPT = PROMPT_PATH.read_text(encoding="utf-8").rstrip("\n")
FORBIDDEN_FIELDS = frozenset({"ground_truth", "injection_task", "security", "is_attack", "goal", "utility"})


@dataclass(frozen=True)
class ReviewerView:
    """Only the information a human reviewer could see at decision time."""

    user_task: str
    tool: str
    args: dict[str, Any]
    recent_tool_output: str


def _tool_text(message: dict[str, Any]) -> str:
    if message.get("error"):
        return f"ERROR: {message['error']}"
    content = message.get("content") or []
    if isinstance(content, str):
        return content
    return " ".join(
        str(block.get("content", block.get("text", "")))
        for block in content
        if isinstance(block, dict) and block.get("type", "text") == "text"
    )


def build_view(call: Any, context: Any) -> ReviewerView:
    """Construct an allowlisted view; never access environment or oracle fields."""
    recent = ""
    for message in reversed(context.messages):
        if isinstance(message, dict) and message.get("role") == "tool":
            recent = _tool_text(message)[:MAX_OUTPUT_CHARS]
            break
    return ReviewerView(
        user_task=str(context.user_task),
        tool=str(call.function),
        args=dict(call.args),
        recent_tool_output=recent,
    )


def render_prompt(view: ReviewerView) -> str:
    return PROMPT.format(
        user_task=view.user_task,
        tool=view.tool,
        args=json.dumps(view.args, sort_keys=True, default=str),
        recent=view.recent_tool_output,
    )


def _parse_response(text: str) -> tuple[Decision, str, str | None]:
    matches = re.findall(r"\{.*?\}", text or "", re.S)
    if len(matches) != 1:
        return Decision.DENY, "", "unparsable_or_ambiguous_json"
    try:
        payload = json.loads(matches[0])
    except (ValueError, TypeError):
        return Decision.DENY, "", "invalid_json"
    if not isinstance(payload, dict) or not isinstance(payload.get("decision"), str):
        return Decision.DENY, "", "missing_or_invalid_decision"
    decision = payload["decision"].strip().upper()
    if decision == "APPROVE":
        return Decision.APPROVE, str(payload.get("reason", "")), None
    if decision == "DENY":
        return Decision.DENY, str(payload.get("reason", "")), None
    return Decision.DENY, str(payload.get("reason", "")), "unsupported_decision"


class LLMReviewer:
    """Review escalated calls using a callable returning text or token-count tuple.

    The callback may return plain text, or `(text, input_tokens, output_tokens)`
    for meter integration. Reviewer history distinguishes an actual DENY from an
    API/parse failure that was converted to DENY fail-closed.
    """

    def __init__(self, llm_fn: Callable[[str], Any], meter: Any = None):
        self.llm_fn = meter.wrap("reviewer", llm_fn) if meter is not None else llm_fn
        self.history: list[dict[str, Any]] = []

    def review(self, call: Any, context: Any) -> Decision:
        safe_args = dict(getattr(call, "args", {}) or {})
        record: dict[str, Any] = {
            "tool": getattr(call, "function", None),
            "args": safe_args,
            "decision": Decision.DENY.value,
            "reason": "",
            "error": None,
        }
        try:
            prompt = render_prompt(build_view(call, context))
            output = self.llm_fn(prompt)
            if isinstance(output, tuple):
                output = output[0]
            decision, reason, error = _parse_response(str(output or ""))
            record.update(decision=decision.value, reason=reason, error=error)
        except Exception as error:
            decision = Decision.DENY
            record["decision"] = decision.value
            record["error"] = f"{type(error).__name__}: {str(error)[:300]}"
        self.history.append(record)
        return decision
