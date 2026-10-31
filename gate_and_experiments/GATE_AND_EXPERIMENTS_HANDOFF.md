# Gate and Experiment Infrastructure Handoff

## Scope

This area owns shared approval-gate orchestration and experiment infrastructure. Policy logic is in `approval_policy/`, reviewer prompting is in `reviewer_and_benchmark/`, and benchmark task definitions are provided by AgentDojo.

## Deliverables in This Folder

- `contracts.py`: stable data types and structural interfaces.
- `gate.py`: per-task gate, baselines, decision audit records, deterministic reviewer test double.
- `agentdojo_adapter.py`: AgentDojo 0.1.35 state initializer, gated tool executor, and pipeline assembly function.
- `run_agentdojo.py`: CLI task selector and `run_task_with_pipeline()` entry point.
- `experiments.py`: generic policy/budget/case sweep.
- `metrics.py`: safe TCR, HITL Load, escalation requests, reviewer calls used, and error counts.
- `meter.py`: token/cost totals by component.
- Tests covering decisions, budget enforcement, fail-closed reviewer behavior, metrics, and adapter assembly.

## Integration Sequence

1. The gate/experiment area presents `contracts.py` and confirms decision/context fields with the policy and reviewer/benchmark areas.
2. Select `agentdojo-tiered` or `agentdojo-task0-classifier-tiered` for the developed AgentDojo policy comparison; the latter is specialized to `user_task_0`. `invoice-tiered` is used only by the custom invoice harness.
3. Use the LLM reviewer for evaluation runs. The offline `RuleReviewer` is for harness checks only; reviewer context must exclude benchmark oracle fields.
4. The AgentDojo sweep returns per-run utility, attack outcome, escalation proposals, reviewer calls used, and gate records.
5. Compare policies using the same task/attack matrix, model setup, and escalation budgets.
6. Capture meter output around agent and reviewer API calls; report zero usage explicitly for rule-based policies.
7. Check that each task creates fresh `GateState`, that no policy/reviewer receives oracle labels, and that failures are counted separately from attack success.

## Decision and Logging Rules

- The gate is responsible for budget enforcement, not policy prioritization.
- Each actual reviewer invocation increments `used` once.
- A later escalation after the budget is exhausted becomes denial and does not invoke the reviewer.
- Aggregate escalation requests count all policy `ESCALATE` decisions; reviewer calls used count only those admitted by the budget.
- Reviewer exceptions and invalid decisions fail closed to denial.
- Only final `APPROVE` authorizes the AgentDojo adapter to execute a tool.
- Logs must include enough information to inspect the proposed action and trace the final decision. Do not copy oracle labels into `DecisionContext.metadata`.

## Validation

Run from the project root:

```bash
python -m pip install -r gate_and_experiments/requirements.txt
python -m pytest approval_policy/tests gate_and_experiments/tests reviewer_and_benchmark/tests -q
python -m compileall -q approval_policy gate_and_experiments reviewer_and_benchmark
```

The adapter assembly test uses a fake LLM element; no credentials are needed. Real AgentDojo model evaluation must be run separately with a configured provider and must be recorded as online evaluation. Never commit secrets or claim that synthetic tests are benchmark outcomes.

The task runner is available as `python -m gate_and_experiments.run_agentdojo --task user_task_0 --policy agentdojo-tiered --budget 1`. It selects the task and invokes AgentDojo; the agent and LLM reviewer require provider credentials. Add `--offline-reviewer` only for harness checks, not final evaluation.

## Known Boundary

`build_agentdojo_pipeline()` accepts an already-created AgentDojo `BasePipelineElement` for the LLM. This keeps model/provider construction with the benchmark runner and avoids credentials in this folder. The adapter assembly was checked against AgentDojo `0.1.35`; the group still needs to validate an online run and confirm the chosen benchmark suite's injection setup.
