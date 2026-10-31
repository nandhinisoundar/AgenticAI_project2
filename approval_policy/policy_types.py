"""Policy assessment record compatible with the shared gate contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from gate_and_experiments.contracts import Decision


@dataclass(frozen=True)
class Assessment:
    """Decision plus a safe reason and structured, non-oracle evidence summary."""

    decision: Decision
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
