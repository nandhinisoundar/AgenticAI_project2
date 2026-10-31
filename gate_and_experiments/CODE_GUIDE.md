# Gate and Experiment Code Guide

This guide explains the implementation, tests, and configuration in `gate_and_experiments/`. The project has separate functional areas: `approval_policy/` contains evidence-grounded policies and invoice checks, while `reviewer_and_benchmark/` contains the LLM reviewer and reviewer documentation.

## Big Picture

The gate and experiment area owns shared gate and evaluation plumbing. The approval-policy area supplies the decision policies; the reviewer/benchmark area supplies the LLM reviewer. AgentDojo supplies benchmark tasks and attacks. The code is divided into a framework-independent core plus an AgentDojo adapter:

```text
AgentDojo proposes a call
        |
        v
agentdojo_adapter.AgentDojoGatedExecutor
        |
        v
contracts.ToolCall + contracts.DecisionContext
        |
        v
gate.Gate.decide()
        |
        +--> policy.decide() -> APPROVE / DENY / ESCALATE
        |
        +--> on ESCALATE: reviewer.review(), if budget remains
        |
        v
Audit record
        |
        +--> execute tool only for final APPROVE
        +--> otherwise return a blocked tool result
```

The gate never executes a tool itself. That separation allows its decisions and budget behavior to be unit-tested without a live AgentDojo model or external side effects.

## Source Files

### `contracts.py`

Defines stable interfaces and data structures used across contributor boundaries.

- `Decision`: string enum with `APPROVE`, `DENY`, and `ESCALATE`. Policies and reviewers return these values, and the gate writes their string values into audit records.
- `ToolCall`: framework-independent representation of a proposed call. It stores `call_id`, `function`, and an argument dictionary. The adapter converts AgentDojo's tool-call representation into this object.
- `DecisionContext`: decision-time information: the user task, conversation messages, optional runtime environment, escalation budget/count, and extensible metadata. Do not place benchmark-only labels in context passed to the policy or reviewer.
- `GateState`: mutable state for one task. `budget` is `k`, `used` is the number of reviewer calls made, and `records` stores decisions. `__post_init__` rejects a negative budget.
- `ApprovalPolicy`: structural protocol implemented by the policy classes in `approval_policy/`.
- `Reviewer`: structural protocol for the reviewer area's `review(call, context)` implementation.

Python protocols allow the policy and reviewer areas to provide their own classes without inheriting from the gate code, as long as they implement the required method and return types.

### `approval_policy/tiered_policy.py`

`TieredPolicy` provides shared evidence-grounded decision logic. `AgentDojoTieredPolicy` and `InvoiceTieredPolicy` expose it under benchmark-specific identities, while `AgentDojoTask0ClassifierPolicy` adds fixed call categories for AgentDojo `user_task_0`. The tiered logic approves read-only calls and authorized writes, denies unrequested or conflicting writes, and escalates when evidence is incomplete or ambiguous. Focused policy tests are in `approval_policy/tests/`.

### `gate.py`

Implements the framework-independent decision process.

- `FullApprove.decide()`: required baseline; always returns `APPROVE`.
- `FullEscalate.decide()`: required baseline; always returns `ESCALATE`, leaving review-budget enforcement to the gate.
- `DenyAll.decide()`: optional control policy; always returns `DENY`.
- `Gate.__init__(policy, reviewer)`: stores the selected strategy and reviewer.
- `Gate.decide(call, context, state, task_id)`: asks the policy for a decision, handles escalation and budget, invokes the reviewer when allowed, fails closed if the reviewer raises or returns an invalid decision, and appends an audit record. It returns the record but never executes the tool.
- `Gate.fresh_state(budget)`: creates isolated state for a new task. Call this at every task boundary; do not share one `GateState` across runs.
- `RuleReviewer.review()`: deterministic offline test double that approves only when a proposed recipient string appears in the user task. It is for testing plumbing, not a substitute for the LLM reviewer or a benchmark result.

A reviewer call consumes one unit of budget before it is attempted. If the reviewer fails, that unit remains used and the decision is denied; this is conservative accounting because an intervention was attempted. If `used >= budget`, the reviewer is not called and the gate denies the call. Aggregate escalation requests count every policy `ESCALATE`; reviewer calls used count only requests admitted by the budget.

Each record includes task/call IDs, tool, arguments, proposed and final decision, deciding component, reason, reviewer calls used, and budget. A policy escalation denied after budget exhaustion is still an escalation request but does not increment reviewer calls used.

### `agentdojo_adapter.py`

Contains the AgentDojo-specific translation and pipeline integration.

- `GateStateInitializer`: a `BasePipelineElement` placed before the agent. Its `query()` creates a new `GateState` under the `approval_gate` key in `extra_args`, resetting the budget for every task invocation.
- `AgentDojoGatedExecutor`: subclasses AgentDojo's `ToolsExecutor`. Its `query()` returns early if there is no assistant tool call. Otherwise it converts each AgentDojo call into a `ToolCall`, creates a `DecisionContext`, invokes `Gate.decide()`, and then:
  - Calls the runtime and formats the result if final decision is `APPROVE`.
  - Returns a blocked tool result if final decision is `DENY`.
  - Preserves one result per proposed call, including calls in a multi-call assistant response.
- `build_agentdojo_pipeline(llm, gate, budget, system_message)`: assembles the pipeline in the tested 0.1.35 order: system message, `InitQuery`, gate-state initializer, model element, then a `ToolsExecutionLoop` containing the gated executor followed by the same model element.

The function takes an already-created LLM element. This keeps model/provider configuration and credentials outside the gate code. Its pipeline shape has a fake-model test; a real online run still requires credentials and validation with the chosen task suite.

### `run_agentdojo.py`

This command-line runner fills the task-selection gap. Its arguments select the AgentDojo suite/version, user-task ID, optional injection-task ID, gate policy, escalation budget, and model. `run_one()` loads the suite, retrieves the selected task objects, constructs a configured LLM element, builds the gate pipeline, creates an optional direct-attack payload, and calls `suite.run_task_with_pipeline()`. It returns the prompt, utility/security outcomes, escalation usage, and gate records. The CLI currently requires explicit `--offline-reviewer` and uses `RuleReviewer`; this is a harness smoke runner, not the assignment's online LLM reviewer evaluation. The agent model still requires valid provider credentials.

### `experiments.py`

Provides policy/budget/case sweep orchestration without importing a particular task suite.

- `CaseRunner`: callback shape for benchmark-specific execution. The benchmark area can implement this for the banking or invoice suite.
- `run_sweep(policies, budgets, cases, run_case)`: loops over every policy, budget, and case. The callback receives `(policy_instance, budget, case)` and should return a dictionary containing at least `utility`, `attack_succeeded`, and `escalations`.
- Exceptions are captured as per-case error rows, so one runtime failure does not abort all remaining experiments. Those rows are marked `error=True`, not falsely labeled as attack successes.
- The function returns both raw per-run results and summary rows from `metrics.summarize()`.

For fair comparisons, provide the same case matrix for every policy and report errors and denominator treatment.

### `metrics.py`

Computes core aggregate results from a list of run dictionaries.

- `tcr_at_k(runs)`: counts safe task completions divided by non-error, task-eligible runs. Attack-only cases are excluded; if there are no eligible runs, it returns `0.0`.
- `hitl_load(runs)`: counts reviewer calls used on successful, attack-free, task-eligible runs.
- `total_escalations(runs)`: counts all proposed `ESCALATE` decisions over non-error runs, including those denied after budget exhaustion.
- `summarize(results)`: generates sorted rows by policy and budget with TCR, HITL, total escalation requests, reviewer calls used, error count, and run count.

The report should disclose that errored runs are excluded from the TCR denominator and show their count separately.

### `meter.py`

Tracks token usage and optional estimated dollar cost by component.

- `UsageMeter(prices_per_million=None)`: accepts optional per-component input/output price rates per million tokens.
- `record(component, input_tokens, output_tokens, calls=1)`: adds validated non-negative usage under labels such as `agent`, `policy`, or `reviewer`.
- `wrap(component, fn)`: wraps a callback returning `(result, input_tokens, output_tokens)`, records the usage, and returns the original result.
- `report()`: returns counts and, when pricing is configured, estimated USD cost per component.

For any rule-based component, report zero LLM usage. Meter every model request; this module does not automatically intercept provider SDK calls unless they are passed through `wrap()` or recorded explicitly.

## Test Files

### `tests/test_gate.py`

Uses fake policies/reviewers and tool calls; it does not call an LLM. Tests:

- The three baseline decision values.
- Successful escalation and reviewer approval.
- Budget exhaustion converting a later escalation to denial without increasing use.
- Reviewer exception and invalid reviewer return value failing closed.
- Independent state objects for separate tasks.
- Negative-budget rejection.

### `tests/test_agentdojo_adapter.py`

Uses a fake `BasePipelineElement` instead of a provider model. Tests:

- The initializer creates fresh per-task state.
- The pipeline contains the expected setup elements and puts `AgentDojoGatedExecutor` before the LLM in the tool loop.

This checks assembly shape, not live model execution or benchmark score.

### `tests/test_run_agentdojo.py`

Uses a fake suite/model to verify task selection is passed to the AgentDojo task runner and results are returned without model credentials. It does not test a live model call.

### `tests/test_metrics_meter.py`

Tests:

- Safe completion rate, HITL load, total escalations, and error counting.
- Token and estimated-cost calculations.
- Sweep behavior when one case raises an exception.

## Configuration and Documentation Files

- `requirements.txt`: pins AgentDojo 0.1.35 and declares pytest. Install it from the project root using `python -m pip install -r gate_and_experiments/requirements.txt`.
- `.gitignore`: excludes Python caches, pytest cache, virtual environments, `.env`, and compiled Python files.
- `README.md`: scope, interface contracts, local test commands, and AgentDojo assembly notes.
- `GATE_AND_EXPERIMENTS_HANDOFF.md`: ordered integration checklist and collaboration handoff expectations.

The policy's usage and test guidance is in `approval_policy/README.md`.

## Commands

Run from the overall project root:

```bash
python -m pip install -r gate_and_experiments/requirements.txt
python -m pytest gate_and_experiments -q
python -m compileall -q gate_and_experiments
```

The unit and adapter tests do not require API credentials. Real model evaluation does.

## Reserved Contributor Folders

`approval_policy/` and `reviewer_and_benchmark/` are intentionally empty at this stage. The developed policy should implement the `ApprovalPolicy` contract. The reviewer should implement the `Reviewer` contract and exclude all oracle-only data; the benchmark area should also provide benchmark cases/run callbacks. The root [README.md](../README.md) contains the functional ownership plan and overall assignment integration checklist.
