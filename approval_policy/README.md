# Developed Approval Policy

`TieredPolicy` contains the shared evidence-grounded authorization logic and implements the shared `ApprovalPolicy` contract from `gate_and_experiments/contracts.py`. Two named policy identities use that logic in their respective evaluation modes: `AgentDojoTieredPolicy` (`agentdojo-tiered`) for AgentDojo banking tasks and `InvoiceTieredPolicy` (`invoice-tiered`) for the custom invoice benchmark. They can evolve independently if their domain-specific evidence or authorization requirements diverge.

- `tiered_policy.py` evaluates proposed tool operations and returns `APPROVE`, `DENY`, or `ESCALATE`.
- `evidence.py` parses decision-visible tool results and execution history. It does not inspect AgentDojo's hidden environment or oracle labels.
- `authorization.py` derives bounded authorizations from user intent and visible evidence.
- `policy_types.py` defines policy assessment and reason/evidence metadata.
- `tests/test_tiered_policy.py` tests policy decisions, including untrusted document content and evidence ambiguity.
- `tests/test_invoice_extraction.py` tests invoice candidate extraction.

The gate owns escalation-budget enforcement and reviewer invocation. The policy does not execute tools. See the [gate and experiment guide](../gate_and_experiments/README.md) for running the AgentDojo benchmark and complete test suite.
