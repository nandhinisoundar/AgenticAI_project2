"""Report-oriented analysis for benchmark run records and gate trajectories."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from gate_and_experiments.metrics import hitl_load, tcr_at_k, total_escalations


def _gate_records(run: dict[str, Any]) -> list[dict[str, Any]]:
    return list(run.get("gate_records") or [])


def _case_label(run: dict[str, Any]) -> str:
    return str(run.get("case_id") or run.get("task_id") or "case")


def _reviewed_escalation_records(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        record for record in _gate_records(run)
        if record.get("proposed") == "ESCALATE"
        and record.get("by") in {"reviewer", "reviewer error"}
    ]


def _escalation_signature(run: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted({str(record.get("tool", "unknown")) for record in _reviewed_escalation_records(run)}))


def _escalation_request_signature(run: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted({
        str(record.get("tool", "unknown"))
        for record in _gate_records(run)
        if record.get("proposed") == "ESCALATE"
    }))


def build_tcr_hitl_curve(
    results: dict[tuple[str, int], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Return one TCR/HITL row per policy and budget with its denominator."""
    rows = []
    for (policy, budget), runs in sorted(results.items()):
        eligible = [
            run for run in runs
            if not run.get("error", False) and run.get("tcr_eligible", True)
        ]
        rows.append({
            "policy": policy,
            "k": budget,
            "tcr": round(tcr_at_k(runs), 4),
            "hitl_load": hitl_load(runs),
            "eligible_clean_runs": len(eligible),
            "successful_clean_runs": sum(
                bool(run.get("utility")) and not bool(run.get("attack_succeeded"))
                for run in eligible
            ),
            "errors": sum(bool(run.get("error")) for run in runs),
            "total_escalations": total_escalations(runs),
            "reviewer_calls_used": sum(
                int(run.get("reviewer_calls_used", run.get("reviewer_calls", 0)))
                for run in runs if not run.get("error", False)
            ),
        })
    return rows


def _trajectory(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "step": index,
            "tool": record.get("tool"),
            "args": record.get("args", {}),
            "proposed": record.get("proposed"),
            "final": record.get("final"),
            "by": record.get("by"),
            "policy_reason": record.get("policy_reason"),
            "reason": record.get("reason"),
            "used": record.get("used"),
            "budget": record.get("budget"),
            "invoked": record.get("invoked", False),
        }
        for index, record in enumerate(_gate_records(run), start=1)
    ]


def classify_failures(
    results: dict[tuple[str, int], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Return candidate failure counts and representative trajectories.

    Outcome-only records cannot prove an approved call was unsafe or a denied
    call was safe. Such cases are explicitly marked as candidates for manual
    review instead of being presented as adjudicated errors.
    """
    conservative: list[dict[str, Any]] = []
    permissive: list[dict[str, Any]] = []
    clean_total = 0
    attack_total = 0
    errors = 0
    for (policy, budget), runs in sorted(results.items()):
        for run in runs:
            if run.get("error", False):
                errors += 1
                continue
            if run.get("tcr_eligible", True):
                clean_total += 1
                if not run.get("utility"):
                    conservative.append({
                        "policy": policy,
                        "k": budget,
                        "case": _case_label(run),
                        "failure_kind": "clean task not completed safely; candidate over-conservative or agent failure",
                        "trajectory": _trajectory(run),
                    })
            if run.get("attack_id"):
                attack_total += 1
                if run.get("attack_succeeded"):
                    permissive.append({
                        "policy": policy,
                        "k": budget,
                        "case": _case_label(run),
                        "failure_kind": "attack goal succeeded; inspect approved write effects",
                        "trajectory": _trajectory(run),
                    })
    return {
        "over_conservative_candidate_count": len(conservative),
        "clean_case_count": clean_total,
        "over_conservative_candidates": conservative,
        "over_permissive_candidate_count": len(permissive),
        "attack_case_count": attack_total,
        "over_permissive_candidates": permissive,
        "error_count": errors,
    }


def escalation_value_analysis(
    results: dict[tuple[str, int], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Compare adjacent budgets and report outcome changes with escalation signatures."""
    by_policy: dict[str, dict[int, list[dict[str, Any]]]] = defaultdict(dict)
    for (policy, budget), runs in results.items():
        by_policy[policy][budget] = runs

    rows: list[dict[str, Any]] = []
    for policy, budget_runs in sorted(by_policy.items()):
        budgets = sorted(budget_runs)
        for lower_k, higher_k in zip(budgets, budgets[1:]):
            low_runs = {_case_label(run): run for run in budget_runs[lower_k]}
            high_runs = {_case_label(run): run for run in budget_runs[higher_k]}
            shared_case_ids = sorted(low_runs.keys() & high_runs.keys())
            case_pairs = [(case_id, low_runs[case_id], high_runs[case_id]) for case_id in shared_case_ids]
            improved: list[str] = []
            regressed: list[str] = []
            for case, low, high in case_pairs:
                if low.get("error") or high.get("error"):
                    continue
                if low.get("attack_id"):
                    low_safe = not bool(low.get("attack_succeeded"))
                    high_safe = not bool(high.get("attack_succeeded"))
                else:
                    low_safe = bool(low.get("utility"))
                    high_safe = bool(high.get("utility"))
                if high_safe and not low_safe:
                    improved.append(case)
                elif low_safe and not high_safe:
                    regressed.append(case)

            rows.append({
                "policy": policy,
                "from_k": lower_k,
                "to_k": higher_k,
                "shared_cases": len(case_pairs),
                "improved_cases": len(improved),
                "regressed_cases": len(regressed),
                "improved_case_ids": improved,
                "regressed_case_ids": regressed,
                "escalation_signatures_at_lower_k": {
                    case: _escalation_signature(low_runs[case])
                    for case in improved
                },
                "escalation_requests_at_lower_k": {
                    case: _escalation_request_signature(low_runs[case])
                    for case in improved
                },
                "reviewer_calls_added": sum(
                    int(high_runs[case].get("reviewer_calls_used", high_runs[case].get("reviewer_calls", 0)))
                    - int(low_runs[case].get("reviewer_calls_used", low_runs[case].get("reviewer_calls", 0)))
                    for case in shared_case_ids
                ),
                "interpretation": (
                    "Outcome comparison only; inspect the listed cases and gate trajectories before attributing benefit to review."
                ),
            })
    return rows
