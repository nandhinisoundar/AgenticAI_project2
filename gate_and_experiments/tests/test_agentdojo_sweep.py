from gate_and_experiments import agentdojo_sweep


def test_sweep_expands_cases_policies_budgets_and_summarizes(monkeypatch):
    seen = []

    def fake_run_one(**kwargs):
        seen.append(kwargs)
        return {
            "task_id": kwargs["task_id"],
            "attack_id": kwargs["attack_id"],
            "utility": kwargs["policy_name"] != "full-escalate",
            "attack_succeeded": False,
            "escalations_used": 1 if kwargs["policy_name"] == "agentdojo-tiered" else 0,
            "gate_records": [],
            "tokens": {"reviewer": {"calls": 2, "input_tokens": 30, "output_tokens": 8}},
            "reviewer_history": [{"decision": "DENY", "error": None}],
            "messages": [{"role": "user", "content": "task"}],
            "reviewer": "RuleReviewer (offline test double)",
            "evaluation_kind": "offline-reviewer harness run",
        }

    monkeypatch.setattr(agentdojo_sweep, "run_one", fake_run_one)
    results, summary = agentdojo_sweep.run_benchmark_sweep(
        suite_version="v1.2.2",
        suite_name="banking",
        task_ids=["user_task_0", "user_task_1"],
        attack_ids=[None, "injection_task_0"],
        policy_names=["agentdojo-tiered", "full-escalate"],
        budgets=[0, 1],
        model="fake-model",
        reviewer_model="fake-reviewer",
        offline_reviewer=True,
    )

    assert len(seen) == 16
    assert len(results) == 4
    assert len(summary) == 4
    assert all(len(runs) == 4 for runs in results.values())
    assert all(row["reviewer_calls"] == 8 for row in summary)
    assert all(row["reviewer_input_tokens"] == 120 for row in summary)
    assert all(row["reviewer_output_tokens"] == 32 for row in summary)
    assert {kwargs["offline_reviewer"] for kwargs in seen} == {True}
    # HITL counts reviewer calls on successful CLEAN runs only (2 clean runs x 1 call).
    assert any(row["policy"] == "agentdojo-tiered" and row["hitl_load"] == 2 for row in summary)
    assert all(row["clean_runs"] == 2 and row["attack_runs"] == 2 for row in summary)


def test_suite_choices_reads_installed_agentdojo_ids(monkeypatch):
    class Suite:
        user_tasks = {"user_task_0": object(), "user_task_2": object()}
        injection_tasks = {"injection_task_1": object()}

    monkeypatch.setattr(agentdojo_sweep, "get_suite", lambda version, name: Suite())
    assert agentdojo_sweep.suite_choices("v1.2.2", "banking") == (
        ["user_task_0", "user_task_2"],
        ["injection_task_1"],
    )


def test_sweep_distinguishes_escalation_requests_from_budget_consumed(monkeypatch):
    def fake_run_one(**kwargs):
        return {
            "task_id": kwargs["task_id"],
            "attack_id": kwargs["attack_id"],
            "utility": False,
            "attack_succeeded": False,
            "escalations_used": 1,
            "budget": kwargs["budget"],
            "gate_records": [
                {"proposed": "ESCALATE", "by": "reviewer"},
                {"proposed": "ESCALATE", "by": "budget exhausted"},
                {"proposed": "DENY", "by": "policy"},
            ],
            "tokens": {},
        }

    monkeypatch.setattr(agentdojo_sweep, "run_one", fake_run_one)
    results, summary = agentdojo_sweep.run_benchmark_sweep(
        suite_version="v1.2.2",
        suite_name="banking",
        task_ids=["user_task_0"],
        attack_ids=[None],
        policy_names=["agentdojo-task0-classifier-tiered"],
        budgets=[1],
        model="fake-model",
        reviewer_model="fake-reviewer",
        offline_reviewer=True,
    )

    run = results[("agentdojo-task0-classifier-tiered", 1)][0]
    assert run["escalations"] == 2
    assert run["reviewer_calls_used"] == 1
    assert summary[0]["total_escalations"] == 2
    assert summary[0]["reviewer_calls_used"] == 1
    assert summary[0]["hitl_load"] == 0


def test_sweep_cost_rows_use_each_policy_and_keep_unpriced_cost_unknown(monkeypatch):
    def fake_run_one(**kwargs):
        return {
            "task_id": kwargs["task_id"],
            "attack_id": None,
            "utility": True,
            "attack_succeeded": False,
            "escalations_used": 0,
            "gate_records": [],
            "tokens": {
                "agent": {"calls": 1, "input_tokens": 10, "output_tokens": 5, "estimated_cost_usd": 0.02},
                "reviewer": {"calls": 0, "input_tokens": 0, "output_tokens": 0},
            },
        }

    monkeypatch.setattr(agentdojo_sweep, "run_one", fake_run_one)
    _, summary = agentdojo_sweep.run_benchmark_sweep(
        suite_version="v1.2.2",
        suite_name="banking",
        task_ids=["user_task_0"],
        attack_ids=[None],
        policy_names=["agentdojo-tiered", "full-approve"],
        budgets=[1],
        model="fake-model",
        reviewer_model="fake-reviewer",
        offline_reviewer=True,
    )

    assert all(row["policy_estimated_cost_usd"] == 0.0 for row in summary)
    assert all(row["agent_estimated_cost_usd"] == 0.02 for row in summary)
    assert all(row["reviewer_estimated_cost_usd"] is None for row in summary)
    assert all(row["agent_input_tokens"] == 10 for row in summary)


def test_sweep_preserves_unknown_agent_cost_and_zero_rule_policy_cost(monkeypatch):
    def fake_run_one(**kwargs):
        return {
            "task_id": kwargs["task_id"],
            "attack_id": None,
            "utility": True,
            "attack_succeeded": False,
            "escalations_used": 0,
            "gate_records": [],
            "tokens": {
                "agent": {"calls": 1, "input_tokens": 9, "output_tokens": 4},
                "reviewer": {"calls": 0, "input_tokens": 0, "output_tokens": 0},
            },
        }

    monkeypatch.setattr(agentdojo_sweep, "run_one", fake_run_one)
    _, summary = agentdojo_sweep.run_benchmark_sweep(
        suite_version="v1.2.2",
        suite_name="banking",
        task_ids=["user_task_0"],
        attack_ids=[None],
        policy_names=["agentdojo-tiered"],
        budgets=[1],
        model="fake-model",
        reviewer_model="fake-reviewer",
        offline_reviewer=True,
    )

    assert summary[0]["policy_estimated_cost_usd"] == 0.0
    assert summary[0]["agent_estimated_cost_usd"] is None
    assert summary[0]["reviewer_estimated_cost_usd"] is None


def test_suite_injection_definitions_exposes_goal_text_for_display(monkeypatch):
    class Injection:
        GOAL = "Attempt to make an unauthorized payment."

    class Suite:
        injection_tasks = {"injection_task_1": Injection()}

    monkeypatch.setattr(agentdojo_sweep, "get_suite", lambda version, name: Suite())
    assert agentdojo_sweep.suite_injection_definitions("v1.2.2", "banking") == {
        "injection_task_1": "Attempt to make an unauthorized payment."
    }
