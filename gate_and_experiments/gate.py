"""Framework-independent approval gate and reference baselines."""
from __future__ import annotations

from enum import Enum
from typing import Any

from gate_and_experiments.contracts import ApprovalPolicy, Decision, DecisionContext, GateState, Reviewer, ToolCall


def _normalize_decision(value: Any) -> Decision | None:
    """Normalize decisions from reloaded enum classes and string-based adapters."""
    raw_value = value.value if isinstance(value, Enum) else value
    try:
        return Decision(raw_value)
    except (TypeError, ValueError):
        return None


class FullApprove:
    """Assignment baseline that automatically approves every tool call."""

    def decide(self, call: ToolCall, context: DecisionContext) -> Decision:
        return Decision.APPROVE


class FullEscalate:
    """Assignment baseline that requests human review for every tool call."""

    def decide(self, call: ToolCall, context: DecisionContext) -> Decision:
        return Decision.ESCALATE


class DenyAll:
    """Optional control baseline that blocks every call."""

    def decide(self, call: ToolCall, context: DecisionContext) -> Decision:
        return Decision.DENY


class Gate:
    """Apply policy and reviewer decisions while enforcing one task's budget."""

    def __init__(self, policy: ApprovalPolicy, reviewer: Reviewer):
        self.policy = policy
        self.reviewer = reviewer

    def decide(
        self,
        call: ToolCall,
        context: DecisionContext,
        state: GateState,
        task_id: str,
    ) -> dict[str, Any]:
        """Produce a final decision and append an auditable per-call record.

        The gate never executes a tool. An integration adapter must execute a
        call only when the returned `final` value is `APPROVE`.
        """
        call_context = DecisionContext(
            user_task=context.user_task,
            messages=context.messages,
            environment=context.environment,
            escalation_budget=state.budget,
            escalations_used=state.used,
            metadata=context.metadata,
            executed_calls=tuple(context.executed_calls) + tuple(
                record for record in state.records if record.get("invoked")
            ),
        )
        assessment = None
        assess = getattr(self.policy, "assess", None)
        if callable(assess):
            assessment = assess(call, call_context)
            proposed = _normalize_decision(assessment.decision)
        else:
            proposed = _normalize_decision(self.policy.decide(call, call_context))
        if proposed is None:
            proposed = Decision.DENY
            reason = "invalid policy decision"
        else:
            reason = assessment.reason if assessment is not None else "policy"

        final = proposed
        decided_by = "policy"
        if proposed == Decision.ESCALATE:
            if state.used >= state.budget:
                final = Decision.DENY
                decided_by = "budget exhausted"
                reason = "escalation budget exhausted"
            else:
                state.used += 1
                reviewer_context = DecisionContext(
                    user_task=context.user_task,
                    messages=context.messages,
                    environment=context.environment,
                    escalation_budget=state.budget,
                    escalations_used=state.used,
                    metadata=context.metadata,
                    executed_calls=tuple(context.executed_calls) + tuple(
                        record for record in state.records if record.get("invoked")
                    ),
                )
                try:
                    reviewer_decision = _normalize_decision(self.reviewer.review(call, reviewer_context))
                    if reviewer_decision not in {Decision.APPROVE, Decision.DENY}:
                        raise ValueError("Reviewer must return APPROVE or DENY")
                    final = reviewer_decision
                    decided_by = "reviewer"
                    reason = "reviewer decision"
                except Exception as error:
                    final = Decision.DENY
                    decided_by = "reviewer error"
                    reason = f"review failed closed: {type(error).__name__}"

        record = {
            "task_id": task_id,
            "call_id": call.call_id,
            "tool": call.function,
            "args": dict(call.args),
            "proposed": proposed.value,
            "final": final.value,
            "by": decided_by,
            "reason": reason,
            "policy_reason": assessment.reason if assessment is not None else None,
            "policy_evidence": assessment.evidence if assessment is not None else {},
            "used": state.used,
            "budget": state.budget,
            "invoked": False,
            "tool_error": None,
        }
        state.records.append(record)
        return record

    @staticmethod
    def fresh_state(budget: int) -> GateState:
        """Create a new state object for a new task evaluation."""
        return GateState(budget=budget)


class RuleReviewer:
    """Offline reviewer double that approves only user-named recipients."""

    def review(self, call: ToolCall, context: DecisionContext) -> Decision:
        recipient = str(call.args.get("recipient", ""))
        return Decision.APPROVE if recipient and recipient in context.user_task else Decision.DENY
