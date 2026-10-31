"""Stable contracts shared between the project contributors."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, Sequence


class Decision(str, Enum):
    """Decision values used by policies, reviewers, and the gate."""

    APPROVE = "APPROVE"
    DENY = "DENY"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class ToolCall:
    """Framework-independent representation of a proposed tool invocation."""

    call_id: str
    function: str
    args: dict[str, Any]


@dataclass(frozen=True)
class DecisionContext:
    """Decision-time context shared with a policy and its reviewer."""

    user_task: str
    messages: Sequence[Any] = ()
    environment: Any = None
    escalation_budget: int = 0
    escalations_used: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    executed_calls: Sequence[dict[str, Any]] = ()


@dataclass
class GateState:
    """Per-task escalation accounting and decision records."""

    budget: int
    used: int = 0
    records: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.budget < 0:
            raise ValueError("Escalation budget cannot be negative.")


class ApprovalPolicy(Protocol):
    """Structural interface for developed approval policy implementations."""

    def decide(self, call: ToolCall, context: DecisionContext) -> Decision:
        """Select approve, deny, or escalate for a proposed call."""
        ...


class Reviewer(Protocol):
    """Structural interface for simulated human reviewer implementations."""

    def review(self, call: ToolCall, context: DecisionContext) -> Decision:
        """Return approve or deny using only reviewer-visible information."""
        ...
