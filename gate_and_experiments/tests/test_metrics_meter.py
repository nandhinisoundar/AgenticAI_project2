"""Unit tests for experiment metrics and token metering."""
from gate_and_experiments.experiments import run_sweep
from gate_and_experiments.meter import UsageMeter
from gate_and_experiments.metrics import hitl_load, summarize, tcr_at_k, total_escalations


def test_metrics_handle_success_failures_and_errors():
    runs = [
        {"utility": True, "attack_succeeded": False, "escalations": 3, "reviewer_calls_used": 2},
        {"utility": False, "attack_succeeded": False, "escalations": 2, "reviewer_calls_used": 1},
        {"utility": True, "attack_succeeded": True, "escalations": 1, "reviewer_calls_used": 1},
        {"utility": False, "attack_succeeded": False, "escalations": 0, "reviewer_calls_used": 0, "error": True},
    ]
    assert tcr_at_k(runs) == 1 / 3
    assert hitl_load(runs) == 2
    assert total_escalations(runs) == 6
    assert summarize({("tiered", 2): runs})[0]["errors"] == 1


def test_task_completion_excludes_attack_only_cases_and_reports_attack_block_rate():
    rows = summarize({("tiered", 1): [
        {"utility": True, "attack_succeeded": False, "attack_id": None, "escalations": 2, "reviewer_calls_used": 1},
        {"utility": False, "attack_succeeded": False, "attack_id": "injection_task_0", "escalations": 2, "reviewer_calls_used": 1, "tcr_eligible": False},
        {"utility": False, "attack_succeeded": True, "attack_id": "injection_task_1", "escalations": 0, "reviewer_calls_used": 0, "tcr_eligible": False},
    ]})

    assert rows[0]["tcr"] == 1.0
    assert rows[0]["attack_block_rate"] == 0.5
    assert rows[0]["hitl_load"] == 1
    assert rows[0]["total_escalations"] == 4


def test_meter_tracks_components_and_cost():
    meter = UsageMeter({"reviewer": {"input": 2.0, "output": 4.0}})
    wrapped = meter.wrap("reviewer", lambda prompt: ("ok", 500_000, 250_000))
    assert wrapped("prompt") == "ok"
    report = meter.report()["reviewer"]
    assert report["calls"] == 1
    assert report["input_tokens"] == 500_000
    assert report["output_tokens"] == 250_000
    assert report["estimated_cost_usd"] == 2.0


def test_meter_omits_cost_when_component_prices_are_not_configured():
    meter = UsageMeter()
    meter.record("agent", 10, 5)
    report = meter.report()
    assert report["agent"]["input_tokens"] == 10
    assert report["agent"]["output_tokens"] == 5
    assert "estimated_cost_usd" not in report["agent"]


def test_metered_openai_client_records_completion_usage():
    class Usage:
        prompt_tokens = 123
        completion_tokens = 45

    class Response:
        usage = Usage()

    class Completions:
        def create(self, **kwargs):
            return Response()

    class Chat:
        completions = Completions()

    class Client:
        chat = Chat()

    meter = UsageMeter({"agent": {"input": 1.0, "output": 2.0}})
    wrapped = meter.wrap_openai_client(Client(), "agent")
    wrapped.chat.completions.create(model="fake", messages=[])

    usage = meter.report()["agent"]
    assert usage["calls"] == 1
    assert usage["input_tokens"] == 123
    assert usage["output_tokens"] == 45
    assert usage["estimated_cost_usd"] == 0.000213


def test_sweep_records_results_and_captures_case_errors():
    def runner(policy, budget, case):
        if case == "bad":
            raise RuntimeError("test failure")
        return {"utility": True, "attack_succeeded": False, "escalations": budget}

    results, rows = run_sweep({"p": object()}, [0, 1], ["ok", "bad"], runner)
    assert len(results[("p", 0)]) == 2
    assert results[("p", 0)][1]["error"] is True
    assert rows[0]["errors"] == 1
