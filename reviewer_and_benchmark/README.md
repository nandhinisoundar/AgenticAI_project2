# Reviewer and Benchmark Support

This package contains the assignment-facing simulated reviewer and notes about AgentDojo experiment records.

## Simulated Reviewer

`reviewer.py` implements `LLMReviewer`. It builds a strict `ReviewerView` with exactly four fields:

- `user_task`: the user's request.
- `tool`: the proposed tool name.
- `args`: the proposed arguments.
- `recent_tool_output`: latest visible tool output (up to 1,500 characters), or the visible error string.

The reviewer does not read the environment or benchmark oracle fields. The active prompt is `prompts/reviewer_v2.txt`; temperature is fixed at zero by `llm_clients.py`. Only a single valid JSON decision of `APPROVE` or `DENY` can approve a call; provider errors and malformed/unsupported outputs fail closed to `DENY`.

Each reviewer decision is recorded in `LLMReviewer.history`, separating an actual reviewer denial from an error that failed closed. Model adapters return token counts and can be wrapped by `UsageMeter` under the `reviewer` component.

## Benchmark Run Records

The AgentDojo runner records the selected suite/task/attack, policy, budget, agent/reviewer model names, utility/security outcomes, escalation requests, reviewer calls used, gate records, visible conversation trajectory, reviewer history, and available component token usage. Escalation requests include those denied after budget exhaustion; reviewer calls used counts actual budget consumption. The trajectory is for post-run analysis; it is not passed to the policy or reviewer as oracle data.

## Validation

From the project root:

```bash
python -m pytest reviewer_and_benchmark gate_and_experiments approval_policy -q
```

Reviewer unit tests use fake callbacks and do not call a model. Online AgentDojo runs require provider credentials and incur model costs.
