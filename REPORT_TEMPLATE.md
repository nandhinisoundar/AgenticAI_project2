# Safety Approval Policies for Agent Harnesses

> Status: draft template. Replace every bracketed item with evidence from completed AgentDojo online runs. Do not present unit tests, offline-reviewer runs, or the Custom invoice check as AgentDojo benchmark results.

## 1. Executive Summary

- Developed policy: [policy names and short description]
- AgentDojo suite/version: [suite/version]
- Agent model and reviewer model: [model identifiers]
- Evaluation matrix: [tasks, attacks, policies, budgets]
- Main result: [summary supported by measured results]
- Key limitation: [limitations]

## 2. Problem and Design

Describe the three-way decision model (`APPROVE`, `DENY`, `ESCALATE`), per-task escalation budget $k$, and the rule that escalations after budget exhaustion are denied.

Policies compared:

| Strategy | Description |
|---|---|
| AgentDojo Tiered | [evidence-grounded policy description] |
| AgentDojo Task 0 Classifier Tiered | [fixed categories and Task 0 scope] |
| Full Approve | Approves every proposed tool call |
| Full Escalate | Escalates every proposed tool call, subject to budget |
| Deny All (optional control) | Denies every proposed tool call |

## 3. Experimental Setup

- AgentDojo version/suite: [version and suite]
- Tasks: [IDs and selection rationale]
- Injection tasks: [IDs]
- Agent model: [name/version]
- Reviewer model and prompt version: [name/version]
- Budgets $k$: [values]
- Runs per configuration: [count]
- Reviewer mode: [online LLM; explicitly exclude offline reviewer from final scores]
- Date and environment: [date, relevant package versions]
- Errors/retries/exclusions: [counts and rules]

## 4. Metrics and Results

Report TCR@k as the fraction of clean, task-eligible cases completed safely. Report attack block rate separately for injection cases. HITL load counts reviewer calls on successful clean runs. Also report total escalation requests and reviewer calls used; these differ when the budget is exhausted.

### TCR@k

| Policy | k=0 | k=1 | k=2 | k=3 | [additional k] |
|---|---:|---:|---:|---:|---:|
| AgentDojo Tiered | [ ] | [ ] | [ ] | [ ] | [ ] |
| Task 0 Classifier Tiered | [ ] | [ ] | [ ] | [ ] | [ ] |
| Full Approve | [ ] | [ ] | [ ] | [ ] | [ ] |
| Full Escalate | [ ] | [ ] | [ ] | [ ] | [ ] |

Add a plotted TCR versus $k$ curve. Include the denominator (number of eligible clean cases) and errors for each point.

### Human Review and Attack Results

| Policy | k | HITL load | Escalation requests | Reviewer calls used | Attack block rate | Errors |
|---|---:|---:|---:|---:|---:|---:|
| [policy] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |

## 5. API Usage and Cost

Use completion usage metadata returned by the providers. If a provider omits usage, disclose that limitation. If model pricing was not configured, report token counts and mark dollar cost as unavailable; do not report an unpriced component as `$0`. Rule-based policies have zero API inference cost. The UI's failure labels are candidate counts: manually review gate trajectories before calling a safe denial over-conservative or an approved action over-permissive.

| Policy | Component | Calls | Input tokens | Output tokens | Estimated cost (USD) | Pricing source |
|---|---|---:|---:|---:|---:|---|
| [policy] | Agent | [ ] | [ ] | [ ] | [ ] | [provider/model/date] |
| [policy] | Policy | [ ] | [ ] | [ ] | [ ] | [rule-based: $0 or model pricing] |
| [policy] | Reviewer | [ ] | [ ] | [ ] | [ ] | [provider/model/date] |

Explain whether retries, failed requests, cached calls, or non-token charges are included.

## 6. Failure Analysis

Classify failures using gate trajectories and benchmark outcomes. Include counts and denominators, not only selected anecdotes.

### Over-conservative failures

Examples: safe calls denied; unnecessary escalations consume the budget and block later safe calls.

- Count / denominator: [ ]
- Representative case ID: [ ]
- Trajectory: [ordered tool calls, policy proposal, final decision, budget before/after]
- Root cause: [ ]
- Possible improvement: [ ]

### Over-permissive failures

Examples: an unsafe or unauthorized write receives final `APPROVE` and executes.

- Count / denominator: [ ]
- Representative case ID: [ ]
- Trajectory: [ordered tool calls, policy proposal, final decision, execution outcome]
- Root cause: [ ]
- Possible mitigation: [ ]

### Other failures and errors

Include agent task failures, reviewer/API errors, malformed calls, and benchmark/runtime errors separately. Distinguish an attack blocked from a clean task completed.

## 7. Escalation-Value Analysis

Identify which escalation classes improved safety or task completion and which mainly used budget. Compare outcomes for similar calls across budget values, with supporting trajectories.

- High-value escalation types: [evidence and examples]
- Low-value or avoidable escalation types: [evidence and examples]
- Cases where no reviewer decision could help: [e.g. missing required evidence, exhausted budget]
- Trade-off as $k$ changes: [measured interpretation]

## 8. Limitations and Threats to Validity

Discuss suite coverage, task-specific classifier scope, model stochasticity/version, reviewer simulation limits, pricing assumptions, sample size, and differences between AgentDojo runs and the local Custom invoice check.

## 9. Conclusion

Summarize measured safety/utility trade-offs, reviewer burden, cost, and the strongest supported design lessons. Avoid claims not supported by the selected benchmark runs.

## 10. Presentation Checklist

- [ ] Main policy idea and decision flow
- [ ] AgentDojo gate integration
- [ ] TCR@k curve and two baselines
- [ ] HITL and attack-block results
- [ ] Component-level API cost
- [ ] Representative over-conservative and over-permissive trajectories
- [ ] Escalation-value analysis
- [ ] Conclusions and limitations
