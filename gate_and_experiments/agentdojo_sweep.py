"""Run selected AgentDojo task/attack cases across policies and budgets."""
from __future__ import annotations

from typing import Any

from agentdojo.task_suite.load_suites import get_suite

from gate_and_experiments.experiments import run_sweep
from gate_and_experiments.run_agentdojo import DEFAULT_ATTACK, POLICIES, run_one


def suite_choices(suite_version: str, suite_name: str) -> tuple[list[str], list[str]]:
    """Return task and injection IDs only; do not expose oracle labels."""
    suite = get_suite(suite_version, suite_name)
    return sorted(suite.user_tasks), sorted(suite.injection_tasks)


def suite_injection_definitions(suite_version: str, suite_name: str) -> dict[str, str]:
    """Return human-readable injection goals for UI display only.

    These descriptions are not passed to the agent, policy, or reviewer.
    """
    suite = get_suite(suite_version, suite_name)
    return {
        attack_id: str(getattr(task, "GOAL", attack_id))
        for attack_id, task in suite.injection_tasks.items()
    }


def run_benchmark_sweep(
    suite_version: str,
    suite_name: str,
    task_ids: list[str],
    attack_ids: list[str | None],
    policy_names: list[str],
    budgets: list[int],
    model: str,
    reviewer_model: str,
    offline_reviewer: bool,
    prices_per_million: dict[str, dict[str, float]] | None = None,
    attack_type: str = DEFAULT_ATTACK,
) -> tuple[dict[tuple[str, int], list[dict[str, Any]]], list[dict[str, Any]]]:
    """Run the selected matrix through the shared sweep and metric functions."""
    unknown_policies = set(policy_names) - set(POLICIES)
    if unknown_policies:
        raise ValueError(f"Unknown policy selections: {sorted(unknown_policies)}")
    if not task_ids or not policy_names or not budgets:
        raise ValueError("Select at least one task, policy, and escalation budget.")

    cases = [
        {"task_id": task_id, "attack_id": attack_id}
        for task_id in task_ids
        for attack_id in attack_ids
    ]
    policy_specs = {name: name for name in policy_names}
    run_meters: dict[str, dict[str, Any]] = {}

    def run_case(policy_name: str, budget: int, case: dict[str, str | None]) -> dict[str, Any]:
        report = run_one(
            suite_version=suite_version,
            suite_name=suite_name,
            task_id=str(case["task_id"]),
            attack_id=case["attack_id"],
            policy_name=policy_name,
            budget=budget,
            model=model,
            offline_reviewer=offline_reviewer,
            reviewer_model=reviewer_model,
            prices_per_million=prices_per_million,
            attack_type=attack_type,
        )
        return {
            **report,
            "case_id": f"{case['task_id']} / {case['attack_id'] or 'clean'}",
            "escalations": sum(
                record.get("proposed") == "ESCALATE"
                for record in report.get("gate_records", [])
            ),
            "reviewer_calls_used": report.get("escalations_used") or 0,
            "error": False,
            "tcr_eligible": case["attack_id"] is None,
        }

    results, summary = run_sweep(policy_specs, budgets, cases, run_case)
    for cell, runs in results.items():
        component_totals = {
            component: {
                metric: sum(int(run.get("tokens", {}).get(component, {}).get(metric, 0)) for run in runs)
                for metric in ("calls", "input_tokens", "output_tokens")
            }
            for component in ("agent", "policy", "reviewer")
        }
        for row in summary:
            if (row["policy"], row["k"]) == cell:
                row["agent_calls"] = component_totals["agent"]["calls"]
                row["agent_input_tokens"] = component_totals["agent"]["input_tokens"]
                row["agent_output_tokens"] = component_totals["agent"]["output_tokens"]
                row["policy_calls"] = component_totals["policy"]["calls"]
                row["policy_input_tokens"] = component_totals["policy"]["input_tokens"]
                row["policy_output_tokens"] = component_totals["policy"]["output_tokens"]
                row["reviewer_calls"] = component_totals["reviewer"]["calls"]
                row["reviewer_input_tokens"] = component_totals["reviewer"]["input_tokens"]
                row["reviewer_output_tokens"] = component_totals["reviewer"]["output_tokens"]
                row["agent_estimated_cost_usd"] = _component_cost(runs, "agent")
                row["policy_estimated_cost_usd"] = (
                    0.0
                    if row["policy"] in {
                        "agentdojo-tiered",
                        "agentdojo-task0-classifier-tiered",
                        "full-approve",
                        "full-escalate",
                        "deny-all",
                    }
                    else _component_cost(runs, "policy")
                )
                row["reviewer_estimated_cost_usd"] = _component_cost(runs, "reviewer")
                break
    return results, summary


def _component_cost(runs: list[dict[str, Any]], component: str) -> float | None:
    values = [
        run.get("tokens", {}).get(component, {}).get("estimated_cost_usd")
        for run in runs
    ]
    if not values or any(value is None for value in values):
        return None
    return round(sum(float(value) for value in values), 8)
