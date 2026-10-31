# PE6203 Group Project 2: Safety Approval Policies for Agent Harnesses

This repository implements the official assignment: develop and evaluate an approval gate that intercepts AI-agent tool calls in AgentDojo.

## Decision Model

- `APPROVE`: allow the tool call to execute.
- `DENY`: block the call.
- `ESCALATE`: ask a simulated human reviewer, subject to the per-task escalation budget `k`.

When `k` is exhausted, a further escalation becomes `DENY`. The policy and reviewer must not receive benchmark oracle labels, attack annotations, or hidden task outcomes.

## Assignment Components

- `gate_and_experiments/`: shared contracts, gate, AgentDojo adapter and runner, baseline policies, experiment sweeps, metrics, and token-meter utilities.
- `approval_policy/`: shared evidence-grounded tiering logic, exposed as `AgentDojoTieredPolicy` and `InvoiceTieredPolicy`, plus invoice extraction/checking and tests.
- `reviewer_and_benchmark/`: allowlisted LLM reviewer, prompt version, provider adapters, leakage/fail-closed tests, and reviewer design notes.

The Streamlit interface includes both the AgentDojo benchmark workflow and a local **Custom invoice check** workflow. The invoice workflow accepts synthetic TXT/PDF/image fixtures, optionally extracts candidate fields with explicit consent, and sends simulated calls through the gate; it does not connect to a company database or execute a real payment.

## Setup

From the project root:

```bash
python -m pip install -r gate_and_experiments/requirements.txt
```

For online AgentDojo runs, set the provider API key in the local ignored `.env`, for example `OPENAI_API_KEY=...`. Never commit or paste secrets.

## Run the Benchmark Interface

```bash
python -m streamlit run gate_and_experiments/benchmark_app.py
```

The UI selects AgentDojo tasks and optional injection scenarios, policies, and escalation budgets, then reports TCR@k/HITL rows by budget, escalation requests, reviewer calls used, and gate trajectories. Both AgentDojo and Custom invoice results include cross-budget outcome comparisons and representative failure-candidate trajectories. Outcome-only failures are flagged as candidates for manual trajectory adjudication, not asserted as confirmed safe/unsafe actions. Use the LLM reviewer for evaluation; offline `RuleReviewer` is a harness smoke test only. Model calls may incur API charges.

Agent and reviewer completion token usage is captured when the provider returns usage metadata. To estimate USD cost, enter the actual per-million input/output prices in the UI or set `AGENT_INPUT_PRICE_PER_MILLION`, `AGENT_OUTPUT_PRICE_PER_MILLION`, `REVIEWER_INPUT_PRICE_PER_MILLION`, and `REVIEWER_OUTPUT_PRICE_PER_MILLION`. Unconfigured costs are left unknown; rule-based policy inference has zero API cost. See [REPORT_TEMPLATE.md](REPORT_TEMPLATE.md) for the assignment report structure.

Agent usage is metered from OpenAI-compatible chat-completion responses; providers that omit usage metadata will not contribute token counts. The pricing inputs must match the selected provider/model and pricing date. The **Failure classification and escalation value** panel provides run-level candidates and tool-level escalation counts; inspect representative trajectories and adjudicate failure types before using those candidates as report findings.

Run one task from the CLI:

```bash
python -m gate_and_experiments.run_agentdojo \
	--task user_task_0 \
	--policy agentdojo-tiered \
	--budget 1 \
	--model gpt-4o-mini-2024-07-18 \
	--reviewer-model gpt-4o-mini
```

For `user_task_0` comparisons, select `agentdojo-tiered` for the evidence-grounded policy or `agentdojo-task0-classifier-tiered` for the fixed read-only/authorized-write/unrelated-write/ambiguous classifier. The classifier escalates if invoked for a different task ID. Add `--attack injection_task_0` to run with that injection.

Omit `--attack` for a clean task. Add `--offline-reviewer` only for harness checks.

## Tests

```bash
python -m pytest approval_policy/tests gate_and_experiments/tests reviewer_and_benchmark/tests -q
python -m compileall -q approval_policy gate_and_experiments reviewer_and_benchmark
```

Unit/fake-suite tests validate logic and integration shape; they are not online benchmark results. Online runs require provider credentials and may incur costs.

## Required Report and Presentation

Report TCR@k curves for the developed policy, full-approve, and full-escalate; HITL Load; API costs broken down by agent, policy, and reviewer; over-conservative and over-permissive failures with representative trajectories; and evidence-based escalation-value analysis. Submit the code/report through GitHub and present for 10 minutes plus 5 minutes of Q&A.
