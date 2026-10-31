"""Policy/budget sweep orchestration independent of benchmark-specific suites."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from gate_and_experiments.metrics import summarize


CaseRunner = Callable[[Any, int, Any], dict[str, Any]]


def run_sweep(
    policies: dict[str, Any],
    budgets: Iterable[int],
    cases: Iterable[Any],
    run_case: CaseRunner,
) -> tuple[dict[tuple[str, int], list[dict[str, Any]]], list[dict[str, Any]]]:
    """Run each policy across budgets and benchmark cases, preserving errors.

    `run_case(policy, budget, case)` is supplied by the benchmark owner. This
    keeps experiment orchestration independent of policy internals and
    benchmark suite/task definitions.
    """
    case_list = list(cases)
    results: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for policy_name, policy in policies.items():
        for budget in budgets:
            if budget < 0:
                raise ValueError("Escalation budgets must be non-negative.")
            rows = []
            for case in case_list:
                try:
                    result = dict(run_case(policy, budget, case))
                    result.setdefault("error", False)
                except Exception as error:
                    result = {
                        "utility": False,
                        "attack_succeeded": False,
                        "escalations": 0,
                        "error": True,
                        "error_message": f"{type(error).__name__}: {error}",
                    }
                rows.append(result)
            results[(policy_name, budget)] = rows
    return results, summarize(results)
