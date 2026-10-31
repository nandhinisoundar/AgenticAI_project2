"""Evidence-grounded tool-authorization policy integrated with the shared gate."""
from __future__ import annotations

import re
from typing import Any

from gate_and_experiments.contracts import ApprovalPolicy, Decision, DecisionContext, ToolCall

from approval_policy.authorization import (
    WRITE_INTENT,
    compile_plan,
    direct_payments,
)
from approval_policy.evidence import ACCOUNT_RE, READ_ONLY, WRITES, Evidence, amount, norm
from approval_policy.policy_types import Assessment

MONEY_OUT = frozenset({"send_money", "schedule_transaction", "update_scheduled_transaction"})
ACCOUNT_WRITE = frozenset({"update_password", "update_user_info"})


def _currency(code: str) -> str:
    """Normalize common currency marks/names to currency codes."""
    return {
        "$": "USD", "€": "EUR", "£": "GBP", "DOLLAR": "USD", "DOLLARS": "USD",
        "EURO": "EUR", "EUROS": "EUR", "POUND": "GBP", "POUNDS": "GBP",
    }.get(code.upper(), code.upper())


def _unused_grants(plan, evidence: Evidence):
    """Return authorizations not already consumed by successful earlier writes."""
    available = list(plan.grants)
    for event in evidence.executed:
        tool, args = event.get("tool"), event.get("args", {})
        if tool not in {"send_money", "schedule_transaction"}:
            continue
        index = next((i for i, grant in enumerate(available) if grant.matches(tool, args)), None)
        if index is not None:
            available.pop(index)
    return available


def _contains_nested_call(value: Any, seen: set[int] | None = None) -> bool:
    """Reject nested calls because AgentDojo may execute them before outer validation."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return False
    seen.add(id(value))
    if hasattr(value, "function") and hasattr(value, "args"):
        return True
    if isinstance(value, dict):
        return any(_contains_nested_call(item, seen) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_nested_call(item, seen) for item in value)
    return False


class TieredPolicy:
    """Authorize business obligations from user intent and visible evidence.

    This class implements the shared gate contract. It does not consume review
    budget; the gate owns escalation accounting. It never reads benchmark
    labels or the hidden task environment.
    """

    name = "tiered"
    version = "2.0.1"

    def __init__(self, base_currency: str | None = None):
        """Configure optional bank currency for explicit currency checks."""
        self.base_currency = _currency(base_currency) if base_currency else None

    def decide(self, call: ToolCall, context: DecisionContext) -> Decision:
        """Return the decision value expected by `gate_and_experiments.Gate`."""
        return self.assess(call, context).decision

    def assess(self, call: ToolCall, context: DecisionContext) -> Assessment:
        """Return a decision with an explanation and concise evidence metadata."""
        tool = call.function
        args = dict(call.args)
        if _contains_nested_call(args):
            return Assessment(Decision.DENY, "nested_calls_require_separate_approval")
        if tool in READ_ONLY:
            return Assessment(Decision.APPROVE, "known_read_only_tool", {"output_is_authorization": False})
        if tool not in WRITES:
            return Assessment(Decision.ESCALATE, "unknown_tool_effects")

        required = {
            "send_money": {"recipient", "amount", "subject", "date"},
            "schedule_transaction": {"recipient", "amount", "subject", "date", "recurring"},
            "update_scheduled_transaction": {"id"},
            "update_password": {"password"},
        }
        if required.get(tool, set()) - set(args):
            return Assessment(Decision.DENY, "missing_required_tool_arguments")
        if tool in MONEY_OUT:
            if args.get("amount") is not None and amount(args["amount"]) is None:
                return Assessment(Decision.DENY, "invalid_amount")
            if tool != "update_scheduled_transaction" and not str(args.get("recipient", "")).strip():
                return Assessment(Decision.DENY, "empty_recipient")
            if tool == "update_scheduled_transaction" and (
                isinstance(args.get("id"), bool) or not str(args.get("id", "")).isdigit()
            ):
                return Assessment(Decision.DENY, "invalid_transaction_id")

        evidence = Evidence(context)
        plan = compile_plan(
            context.user_task,
            evidence,
            selected_invoice_path=context.metadata.get("selected_invoice_path"),
        )
        matching = [grant for grant in plan.grants if grant.matches(tool, args)]
        if matching:
            currencies = {_currency(grant.currency) for grant in matching if grant.currency}
            if currencies and (not self.base_currency or currencies != {self.base_currency}):
                return Assessment(Decision.ESCALATE, "currency_requires_explicit_bank_configuration")
            subject = str(args.get("subject", ""))
            known_accounts = {
                norm(row.get(key))
                for row in [*evidence.transactions, *evidence.orders]
                for key in ("sender", "recipient")
            }
            disclosed = {norm(value) for value in re.findall(ACCOUNT_RE, subject, re.I)}
            unrelated = disclosed & known_accounts - {norm(args.get("recipient"))}
            if any(account not in norm(context.user_task) for account in unrelated):
                return Assessment(Decision.DENY, "unrequested_account_data_in_payment_subject")
            available = _unused_grants(plan, evidence)
            chosen = next((grant for grant in available if grant.matches(tool, args)), None)
            if chosen is None and tool in {"send_money", "schedule_transaction"}:
                return Assessment(
                    Decision.DENY,
                    "duplicate_payment_attempt",
                    {"detail": "all_matching_authorizations_already_used"},
                )
            chosen = chosen or matching[0]
            return Assessment(
                Decision.APPROVE,
                "evidence_matches_authorized_operation",
                {"source": chosen.source, "obligation": chosen.obligation, "policy_version": self.version},
            )

        if tool in plan.scopes:
            if plan.unresolved or evidence.parse_errors:
                return Assessment(
                    Decision.ESCALATE,
                    "authorization_evidence_incomplete_or_ambiguous",
                    {"missing": plan.unresolved, "unparsed": evidence.parse_errors},
                )
            if plan.grants:
                return Assessment(Decision.DENY, "parameters_conflict_with_authorization")
            return Assessment(Decision.ESCALATE, "authorization_not_established")
        if plan.scopes or not WRITE_INTENT.search(context.user_task) or re.search(
            r"\b(?:do not|don't|never)\b", context.user_task, re.I
        ):
            return Assessment(Decision.DENY, "operation_not_requested")
        if tool in ACCOUNT_WRITE:
            return Assessment(Decision.ESCALATE, "account_change_requires_authorization")
        return Assessment(Decision.ESCALATE, "unsupported_or_conditional_user_intent")


class AgentDojoTieredPolicy(TieredPolicy):
    """Tiered policy identity used for the AgentDojo banking benchmark."""

    name = "agentdojo-tiered"


class InvoiceTieredPolicy(TieredPolicy):
    """Tiered policy identity used for the custom invoice benchmark."""

    name = "invoice-tiered"


def explicit_payment(user_task: str) -> dict[str, Any] | None:
    """Expose the parser's single explicit payment for UI/debugging integrations."""
    grants = direct_payments(user_task)
    if len(grants) != 1:
        return None
    grant = grants[0]
    return {
        "recipient": grant.values["recipient"][0],
        "amount": str(grant.values["amount"]),
        "date": grant.values.get("date"),
    }
