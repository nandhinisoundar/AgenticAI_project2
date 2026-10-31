"""Unit tests for gate decisions and per-task budgets."""
from enum import Enum

from gate_and_experiments.contracts import Decision, DecisionContext, GateState, ToolCall
from gate_and_experiments.gate import DenyAll, FullApprove, FullEscalate, Gate
from approval_policy.policy_types import Assessment


class ApproveReviewer:
    def review(self, call, context):
        return Decision.APPROVE


class InvalidReviewer:
    def review(self, call, context):
        return Decision.ESCALATE


class ExplodingReviewer:
    def review(self, call, context):
        raise RuntimeError("review service unavailable")


class StaleDecision(Enum):
    APPROVE = "APPROVE"
    DENY = "DENY"
    ESCALATE = "ESCALATE"


class StaleEnumPolicy:
    def assess(self, call, context):
        return Assessment(StaleDecision.APPROVE, "stale_enum_approve")


def make_call(call_id="c1"):
    return ToolCall(call_id, "send_money", {"recipient": "GB123", "amount": 10.0})


def test_baselines_return_the_expected_policy_decisions():
    context = DecisionContext("pay invoice")
    assert FullApprove().decide(make_call(), context) == Decision.APPROVE
    assert FullEscalate().decide(make_call(), context) == Decision.ESCALATE
    assert DenyAll().decide(make_call(), context) == Decision.DENY


def test_escalation_spends_budget_once_and_records_reviewer_result():
    gate = Gate(FullEscalate(), ApproveReviewer())
    state = gate.fresh_state(1)
    record = gate.decide(make_call(), DecisionContext("pay GB123"), state, "task-1")
    assert record["final"] == "APPROVE"
    assert state.used == 1
    assert record["by"] == "reviewer"


def test_gate_normalizes_decision_from_stale_enum_class():
    record = Gate(StaleEnumPolicy(), ApproveReviewer()).decide(
        make_call(), DecisionContext("pay GB123"), GateState(0), "task-stale-enum"
    )

    assert record["proposed"] == "APPROVE"
    assert record["final"] == "APPROVE"
    assert record["reason"] == "stale_enum_approve"


def test_budget_exhaustion_denies_without_second_reviewer_call():
    gate = Gate(FullEscalate(), ApproveReviewer())
    state = gate.fresh_state(1)
    gate.decide(make_call("c1"), DecisionContext("pay GB123"), state, "task-1")
    record = gate.decide(make_call("c2"), DecisionContext("pay GB123"), state, "task-1")
    assert record["final"] == "DENY"
    assert record["by"] == "budget exhausted"
    assert state.used == 1


def test_reviewer_error_and_invalid_decision_fail_closed():
    for reviewer in (ExplodingReviewer(), InvalidReviewer()):
        record = Gate(FullEscalate(), reviewer).decide(
            make_call(), DecisionContext("pay GB123"), GateState(1), "task-2"
        )
        assert record["final"] == "DENY"


def test_gate_states_are_isolated_per_task():
    gate = Gate(FullEscalate(), ApproveReviewer())
    state_a = gate.fresh_state(1)
    state_b = gate.fresh_state(1)
    gate.decide(make_call(), DecisionContext("pay GB123"), state_a, "task-a")
    assert state_a.used == 1
    assert state_b.used == 0


def test_negative_budget_is_rejected():
    try:
        GateState(-1)
    except ValueError:
        return
    raise AssertionError("Expected negative budget to be rejected")
