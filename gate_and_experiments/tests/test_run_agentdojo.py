"""Tests that the runner selects tasks and sends them through the gate pipeline."""
from __future__ import annotations

from types import SimpleNamespace

from agentdojo.agent_pipeline import BasePipelineElement
from agentdojo.functions_runtime import EmptyEnv

from approval_policy import AgentDojoTieredPolicy
from gate_and_experiments import run_agentdojo


class FakeLLM(BasePipelineElement):
    """Credential-free LLM stub for testing the runner's task-selection path."""

    name = "fake-llm"

    def query(self, query, runtime, env=EmptyEnv(), messages=None, extra_args=None):
        return query, runtime, env, messages or [], extra_args or {}


class FakeTask:
    """Minimal benchmark task exposing the fields used in the run report."""

    PROMPT = "Check my balance."


class FakeSuite:
    """Record selected IDs and emulate AgentDojo task execution results."""

    def __init__(self):
        self.user_tasks = {"user_task_0": FakeTask()}
        self.injection_tasks = {"injection_task_0": object()}
        self.run_args = None
        self.injection_security_succeeded = True

    def get_user_task_by_id(self, task_id):
        return self.user_tasks[task_id]

    def get_injection_task_by_id(self, task_id):
        return self.injection_tasks[task_id]

    def get_injection_vector_defaults(self):
        return {}

    def run_task_with_pipeline(self, pipeline, user_task, injection_task, injections):
        self.run_args = (pipeline, user_task, injection_task, injections)
        state_initializer = pipeline.elements[2]
        state_initializer.last_state = SimpleNamespace(used=0, records=[])
        return True, self.injection_security_succeeded


def test_runner_selects_task_and_calls_agentdojo_task_runner(monkeypatch):
    assert run_agentdojo.POLICIES["agentdojo-tiered"] is AgentDojoTieredPolicy
    suite = FakeSuite()
    monkeypatch.setattr(run_agentdojo, "get_suite", lambda version, name: suite)
    monkeypatch.setattr(run_agentdojo, "_get_llm_element", lambda model: FakeLLM())

    report = run_agentdojo.run_one(
        suite_version="v1.2.2",
        suite_name="banking",
        task_id="user_task_0",
        attack_id=None,
        policy_name="agentdojo-tiered",
        budget=2,
        model="fake-model",
        offline_reviewer=True,
    )

    assert suite.run_args is not None
    assert suite.run_args[1] is suite.user_tasks["user_task_0"]
    assert suite.run_args[2] is None
    assert report["task_prompt"] == "Check my balance."
    assert report["utility"] is True
    assert report["attack_succeeded"] is False
    assert report["injection_security_succeeded"] is None
    assert report["escalations_used"] == 0


def test_runner_converts_agentdojo_security_result_to_attack_success(monkeypatch):
    suite = FakeSuite()

    class FakeAttack:
        def attack(self, user_task, injection_task):
            return {}

    monkeypatch.setattr(run_agentdojo, "get_suite", lambda version, name: suite)
    monkeypatch.setattr(run_agentdojo, "_get_llm_element", lambda model: FakeLLM())
    monkeypatch.setattr(run_agentdojo, "load_attack", lambda name, suite, pipeline: FakeAttack())

    blocked_report = run_agentdojo.run_one(
        suite_version="v1.2.2", suite_name="banking", task_id="user_task_0",
        attack_id="injection_task_0", policy_name="agentdojo-tiered", budget=1,
        model="fake-model", offline_reviewer=True,
    )
    assert blocked_report["injection_security_succeeded"] is True
    assert blocked_report["attack_succeeded"] is False

    suite.injection_security_succeeded = False
    successful_attack_report = run_agentdojo.run_one(
        suite_version="v1.2.2", suite_name="banking", task_id="user_task_0",
        attack_id="injection_task_0", policy_name="agentdojo-tiered", budget=1,
        model="fake-model", offline_reviewer=True,
    )
    assert successful_attack_report["injection_security_succeeded"] is False
    assert successful_attack_report["attack_succeeded"] is True


def test_llm_reviewer_fails_closed_on_invalid_output(monkeypatch):
    from gate_and_experiments.contracts import Decision, DecisionContext, ToolCall
    from reviewer_and_benchmark.reviewer import LLMReviewer

    reviewer = LLMReviewer(lambda prompt: '{"decision":"MAYBE"}')
    decision = reviewer.review(
        ToolCall("c1", "send_money", {"amount": 10}),
        DecisionContext("Pay the bill", messages=()),
    )
    assert decision == Decision.DENY
    assert reviewer.history[-1]["error"] == "unsupported_decision"


def test_offline_runner_reports_reviewer_and_token_fields(monkeypatch):
    suite = FakeSuite()
    monkeypatch.setattr(run_agentdojo, "get_suite", lambda version, name: suite)
    monkeypatch.setattr(run_agentdojo, "_get_llm_element", lambda model: FakeLLM())
    report = run_agentdojo.run_one(
        suite_version="v1.2.2", suite_name="banking", task_id="user_task_0",
        attack_id=None, policy_name="agentdojo-tiered", budget=1, model="fake-model",
        offline_reviewer=True,
    )
    assert report["reviewer_history"] == []
    assert report["tokens"] == {}
    assert report["messages"] == []
