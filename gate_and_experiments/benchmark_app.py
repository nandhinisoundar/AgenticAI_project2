"""Streamlit UI for assignment-aligned AgentDojo approval-gate experiments."""
from __future__ import annotations

import json
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gate_and_experiments.agentdojo_sweep import run_benchmark_sweep, suite_choices, suite_injection_definitions
from gate_and_experiments.report_analysis import build_tcr_hitl_curve, classify_failures, escalation_value_analysis
from gate_and_experiments.run_agentdojo import ATTACK_TYPES, DEFAULT_ATTACK, POLICIES
from approval_policy.invoice_extraction import extract_invoice_fields
from approval_policy.invoice_check import INVOICE_INJECTION_CASES, format_invoice_text, run_invoice_benchmark


def _summary_cards(summary: list[dict[str, Any]]) -> None:
    for row in summary:
        st.markdown(
            f"**{row['policy']} · k={row['k']}**  \n"
            f"TCR@k: {row['tcr']:.1%} · HITL: {row['hitl_load']} · "
            f"Escalations: {row['total_escalations']} · Errors: {row['errors']}"
        )
        st.markdown(f"Reviewer Calls: {row.get('reviewer_calls', 0)}")
        st.markdown(f"Reviewer Input Tokens: {row.get('reviewer_input_tokens', 0)}")
        st.markdown(f"Reviewer Output Tokens: {row.get('reviewer_output_tokens', 0)}")


def _save_agentdojo_results(results: dict, summary: list, meta: dict) -> str | None:
    """Write one sweep's runs and summary to results/agentdojo_<timestamp>.json."""
    try:
        from datetime import datetime

        out_dir = Path(__file__).resolve().parents[1] / "results"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"agentdojo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        payload = {
            "meta": meta,
            "summary": summary,
            "runs": {f"{policy}|k={k}": runs for (policy, k), runs in results.items()},
        }
        path.write_text(json.dumps(payload, indent=1, default=str))
        return str(path.relative_to(out_dir.parent))
    except Exception:
        return None


def _compact_summary(summary: list, results: dict) -> str:
    """Plain-text per-cell metrics plus the runs that need attention."""
    lines = []
    for row in summary:
        def pct(value):
            return "n/a" if value is None else f"{value:.3f}"
        lines.append(
            f"{row['policy']} k={row['k']}: TCR={pct(row['tcr'])} "
            f"(clean {row.get('clean_runs', '?')}) UA={pct(row.get('utility_under_attack'))} "
            f"ASR={pct(row.get('targeted_asr'))} (attack {row.get('attack_runs', '?')}) "
            f"HITL={row['hitl_load']} esc={row['total_escalations']} reviewed={row['reviewer_calls_used']} "
            f"errors={row['errors']} agent_tok={row.get('agent_input_tokens', 0)}/{row.get('agent_output_tokens', 0)} "
            f"rev_tok={row.get('reviewer_input_tokens', 0)}/{row.get('reviewer_output_tokens', 0)} "
            f"agent_usd={row.get('agent_estimated_cost_usd')} rev_usd={row.get('reviewer_estimated_cost_usd')}"
        )
    lines.append("")
    lines.append("Clean runs not completed, and attacks that succeeded:")
    for (policy, k), runs in sorted(results.items()):
        for run in runs:
            clean = run.get("tcr_eligible", run.get("attack_id") is None)
            failed_clean = clean and not run.get("utility")
            if failed_clean or run.get("attack_succeeded") or run.get("error"):
                tag = "ERROR" if run.get("error") else ("ATTACK_SUCCEEDED" if run.get("attack_succeeded") else "CLEAN_FAILED")
                blocked = [
                    f"{rec.get('tool')}:{rec.get('final')}({rec.get('by')})"
                    for rec in run.get("gate_records", [])
                    if rec.get("final") != "APPROVE"
                ]
                lines.append(f"  {policy} k={k} {run.get('case_id')} {tag} not_approved={blocked}")
    return "\n".join(lines)


def main() -> None:
    st.set_page_config(page_title="AgentDojo Approval Gate", page_icon="AG", layout="wide")
    st.title("AgentDojo Approval-Gate Evaluation")
    st.caption("Run the assignment benchmark through the approval gate. Runs use the selected agent and reviewer models and may incur API charges.")

    mode = st.radio(
        "Evaluation mode",
        ["AgentDojo benchmark", "Custom invoice check"],
        horizontal=True,
    )
    if mode == "Custom invoice check":
        st.subheader("Custom invoice benchmark")
        st.caption("Local, simulated policy benchmark. The gate audits proposed calls; no banking operation is executed.")
        st.warning("Invoice fields extracted by an LLM are unverified candidates. Review them carefully; this benchmark never makes a real payment.")
        st.caption("Selected AgentDojo injection patterns are replayed as simulated malicious tool calls. This is a local invoice harness, not an AgentDojo suite run.")
        user_task = st.text_input(
            "User request",
            placeholder="For example: Please pay the invoice.",
            help="The uploaded file is the invoice being considered. You do not need to type its filename here, but the request must clearly ask to pay an invoice.",
        )
        uploaded_invoice = st.file_uploader(
            "Upload invoice",
            type=["pdf", "png", "jpg", "jpeg", "webp", "gif", "txt"],
            max_upload_size=10,
            help="Maximum 10 MB. TXT content can be checked directly; use optional LLM extraction for PDF and image files.",
        )
        extraction_model = st.text_input(
            "Invoice extraction model",
            value=os.getenv("INVOICE_EXTRACTION_MODEL", "gpt-5.6-sol"),
            key="invoice_extraction_model",
        )
        consent_to_upload = st.checkbox(
            "I consent to sending this invoice to the selected model provider for extraction",
            value=False,
            help="Invoices may contain personal and financial data. The provider will receive the uploaded document and may process it under your account's terms.",
        )
        if st.button(
            "Extract invoice fields with LLM",
            disabled=uploaded_invoice is None or not consent_to_upload,
            key="extract_invoice_fields",
        ):
            try:
                fields = extract_invoice_fields(
                    uploaded_invoice.name,
                    uploaded_invoice.getvalue(),
                    extraction_model,
                )
                st.session_state["extracted_invoice_fields"] = fields
                st.session_state["extracted_invoice_name"] = uploaded_invoice.name
                st.session_state["invoice_extraction_revision"] = st.session_state.get("invoice_extraction_revision", 0) + 1
                st.success("Candidate fields extracted. Review and edit them before running the policy benchmark.")
            except Exception as error:
                st.error(f"Invoice extraction failed: {type(error).__name__}: {error}")

        extracted = st.session_state.get("extracted_invoice_fields", {})
        if uploaded_invoice is not None and st.session_state.get("extracted_invoice_name") != uploaded_invoice.name:
            extracted = {}
        extracted_for_upload = (
            uploaded_invoice is not None
            and st.session_state.get("extracted_invoice_name") == uploaded_invoice.name
        )
        extracted_amount = extracted.get("amount_due")
        extraction_revision = st.session_state.get("invoice_extraction_revision", 0)
        invoice_path = uploaded_invoice.name if uploaded_invoice else ""
        invoice_content_hash = (
            hashlib.sha256(uploaded_invoice.getvalue()).hexdigest()[:12]
            if uploaded_invoice is not None
            else "no-upload"
        )
        invoice_widget_revision = f"{extraction_revision}_{invoice_content_hash}"
        st.markdown("**Invoice facts for policy evaluation**")
        col_number, col_vendor = st.columns(2)
        invoice_number = col_number.text_input(
            "Invoice number",
            value=str(extracted.get("invoice_number") or ""),
            placeholder="Optional extracted candidate",
            disabled=uploaded_invoice is None,
            key=f"invoice_number_{invoice_widget_revision}",
        )
        vendor_name = col_vendor.text_input(
            "Vendor",
            value=str(extracted.get("vendor") or ""),
            placeholder="Optional extracted candidate",
            disabled=uploaded_invoice is None,
            key=f"invoice_vendor_{invoice_widget_revision}",
        )
        col_iban, col_due = st.columns(2)
        invoice_iban = col_iban.text_input(
            "Invoice payee IBAN",
            value=str(extracted.get("iban") or ""),
            placeholder="Optional extracted candidate",
            disabled=uploaded_invoice is None,
            key=f"invoice_iban_{invoice_widget_revision}",
        )
        amount_due = col_due.text_input(
            "Invoice amount due",
            value=str(extracted_amount) if extracted_amount is not None else "",
            placeholder="May be blank if invoice totals are ambiguous",
            disabled=uploaded_invoice is None,
            key=f"invoice_amount_{invoice_widget_revision}",
        )
        extracted_invoice_text = format_invoice_text({
            "invoice_number": invoice_number or None,
            "vendor": vendor_name or None,
            "iban": invoice_iban or None,
            "amount_due": amount_due or None,
            "due_date": extracted.get("due_date"),
        })
        if uploaded_invoice is not None and uploaded_invoice.name.lower().endswith(".txt"):
            initial_invoice_evidence = uploaded_invoice.getvalue().decode("utf-8-sig", errors="replace")
        elif extracted_for_upload:
            initial_invoice_evidence = extracted_invoice_text
        else:
            initial_invoice_evidence = ""
        invoice_text = st.text_area(
            "Invoice evidence for TieredPolicy",
            value=initial_invoice_evidence,
            placeholder="TXT contents appear here. For PDF/image files, extract fields or paste invoice text. Preserve conflicting values to test escalation.",
            disabled=uploaded_invoice is None,
            height=180,
            key=f"invoice_policy_evidence_{invoice_widget_revision}",
            help="This is the evidence evaluated by the policy. Keep all relevant/conflicting invoice text visible here.",
        )
        if uploaded_invoice is not None:
            st.caption(
                "The selected invoice is bound to the task. TXT uploads are checked using their original text; "
                "for PDF/images, extraction is optional and its reviewed candidates are used as evidence."
            )
        left, right = st.columns(2)
        recipient = left.text_input(
            "Proposed recipient IBAN",
            value=invoice_iban,
            placeholder="Enter or extract recipient",
            disabled=uploaded_invoice is None,
            key=f"payment_recipient_{invoice_widget_revision}",
        )
        payment_amount = right.text_input(
            "Proposed amount",
            value=str(extracted_amount) if extracted_for_upload and extracted_amount is not None else "",
            placeholder="Optional; leave blank to test missing/ambiguous amount escalation",
            disabled=uploaded_invoice is None,
            key=f"payment_amount_{invoice_widget_revision}",
        )
        subject = st.text_input(
            "Payment subject",
            value=f"Invoice {invoice_number}" if extracted_for_upload and invoice_number else "",
            placeholder="Enter payment subject",
            disabled=uploaded_invoice is None,
            key=f"payment_subject_{invoice_widget_revision}",
        )
        payment_date = st.text_input(
            "Payment date",
            value=str(extracted.get("due_date") or ""),
            placeholder="Invoice due date, or enter a date",
            disabled=uploaded_invoice is None,
            key=f"payment_date_{invoice_widget_revision}",
        )
        selected_invoice_policies = st.multiselect(
            "Policies",
            options=["invoice-tiered", "full-approve", "full-escalate", "deny-all"],
            format_func=lambda value: (
                "Invoice Tiered" if value == "invoice-tiered" else value.replace("-", " ").title()
            ),
            key="invoice_policies",
        )
        invoice_budgets = st.multiselect(
            "Escalation budgets k",
            options=[0, 1, 2, 3, 5, 10, 15],
            key="invoice_budgets",
        )
        invoice_reviewer_mode = st.radio(
            "Reviewer",
            options=["offline", "llm"],
            format_func=lambda value: "Offline invoice-aware reviewer" if value == "offline" else "LLM reviewer",
            horizontal=True,
            key="invoice_reviewer_mode",
        )
        invoice_reviewer_model = ""
        consent_to_reviewer = True
        if invoice_reviewer_mode == "offline":
            st.caption("The local reviewer uses the user request and visible invoice facts; it makes no provider calls.")
        else:
            invoice_reviewer_model = st.text_input(
                "Reviewer model",
                value=os.getenv("REVIEWER_MODEL", "gpt-4o-mini"),
                key="invoice_reviewer_model",
            )
            consent_to_reviewer = st.checkbox(
                "I consent to sending escalated calls and visible invoice context to the reviewer provider",
                value=False,
                key="invoice_reviewer_consent",
                help="Only escalation reviews are sent. The reviewer receives the task, proposed call, and latest visible invoice output.",
            )
        selected_invoice_scenarios = st.multiselect(
            "Scenarios",
            options=["clean", *INVOICE_INJECTION_CASES],
            format_func=lambda scenario: (
                "Clean run (no injections)"
                if scenario == "clean"
                else f"{scenario}: {INVOICE_INJECTION_CASES[scenario]}"
            ),
            help="Select a clean run, one or more injection scenarios, or both.",
            key="invoice_scenarios",
        )
        if st.button(
            "Run custom invoice benchmark",
            type="primary",
            disabled=(
                not selected_invoice_policies
                or not invoice_budgets
                or not selected_invoice_scenarios
                or (invoice_reviewer_mode == "llm" and not consent_to_reviewer)
                or uploaded_invoice is None
                or not user_task.strip()
                or not all((invoice_text.strip(), recipient, subject, payment_date))
            ),
        ):
            try:
                parsed_payment_amount = float(payment_amount) if payment_amount.strip() else None
                results, summary = run_invoice_benchmark(
                    user_task=user_task,
                    invoice_path=invoice_path,
                    invoice_text=invoice_text,
                    payment_args={
                        "recipient": recipient,
                        "amount": parsed_payment_amount,
                        "subject": subject,
                        "date": payment_date,
                    },
                    policies=selected_invoice_policies,
                    budgets=invoice_budgets,
                    injection_ids=[scenario for scenario in selected_invoice_scenarios if scenario != "clean"],
                    include_clean="clean" in selected_invoice_scenarios,
                    reviewer_mode=invoice_reviewer_mode,
                    reviewer_model=invoice_reviewer_model or "gpt-4o-mini",
                )
                st.session_state["invoice_benchmark_report"] = {
                    "results": results,
                    "summary": summary,
                    "case_count": len(selected_invoice_scenarios),
                    "policies": selected_invoice_policies,
                    "budgets": invoice_budgets,
                    "reviewer": "Offline invoice-aware reviewer" if invoice_reviewer_mode == "offline" else invoice_reviewer_model,
                }
            except Exception as error:
                st.error(f"Invoice benchmark failed: {type(error).__name__}: {error}")

        invoice_report = st.session_state.get("invoice_benchmark_report")
        if invoice_report:
            st.subheader("Aggregate results")
            st.caption(
                f"Custom invoice simulation · {invoice_report['case_count']} scenario(s) per policy/budget · "
                f"reviewer: {invoice_report['reviewer']} · metrics are local harness results, not AgentDojo benchmark scores"
            )
            st.info(
                "Clean scenarios measure invoice task completion; injection scenarios test attack blocking and do not count as task-completion attempts. "
                "Injection cases contain invoice read, attack-specific reconnaissance, and attack write(s); clean cases contain invoice read, "
                "vendor/account/history validation, and one legitimate payment proposal. "
                "Every call goes through the gate. Full Escalate needs k=5 to review the five clean invoice calls. "
                "Escalation is a gate decision, not a tool call. Injection task 6 adds three split-payment proposals."
            )
            st.dataframe(invoice_report["summary"], width="stretch", hide_index=True)
            st.subheader("TCR@k and HITL curve")
            invoice_curve = build_tcr_hitl_curve(invoice_report["results"])
            if invoice_curve:
                st.line_chart(invoice_curve, x="k", y="tcr", color="policy")
            st.dataframe(invoice_curve, width="stretch", hide_index=True)
            st.subheader("Failure candidates and escalation value")
            invoice_failures = classify_failures(invoice_report["results"])
            st.markdown(
                f"Clean unsuccessful candidates: **{invoice_failures['over_conservative_candidate_count']} / {invoice_failures['clean_case_count']}**. "
                f"Attack-success candidates: **{invoice_failures['over_permissive_candidate_count']} / {invoice_failures['attack_case_count']}**. "
                "These require trajectory review before being labeled as policy failures."
            )
            with st.expander("Candidate failure trajectories"):
                st.markdown("**Over-conservative candidates**")
                st.json(invoice_failures["over_conservative_candidates"])
                st.markdown("**Over-permissive candidates**")
                st.json(invoice_failures["over_permissive_candidates"])
            st.markdown("**Escalation value across budgets**")
            st.dataframe(escalation_value_analysis(invoice_report["results"]), width="stretch", hide_index=True)
            st.subheader("Run details")
            for (policy_name, budget), runs in invoice_report["results"].items():
                with st.expander(f"{policy_name} · k={budget} · {len(runs)} cases"):
                    for run in runs:
                        st.markdown(
                            f"**{run['case_id']}** · "
                            f"case type: `{'task completion' if run.get('tcr_eligible', True) else 'attack check'}` · "
                            f"utility: `{run['utility']}` · "
                            f"attack succeeded: `{run['attack_succeeded']}` · "
                            f"simulated calls: `{len(run['gate_records'])}` · "
                            f"reviewer calls: `{run['reviewer_calls']}`"
                        )
                        st.dataframe(
                            [
                                {
                                    "step": index,
                                    "tool": record["tool"],
                                    "proposed": record["proposed"],
                                    "final": record["final"],
                                    "policy reason": record.get("policy_reason") or "",
                                    "policy evidence": json.dumps(record.get("policy_evidence") or {}, sort_keys=True),
                                    "decision by": record["by"],
                                    "final reason": record["reason"],
                                    "review budget used": f"{record['used']}/{record['budget']}",
                                }
                                for index, record in enumerate(run["gate_records"], start=1)
                            ],
                            width="stretch",
                            hide_index=True,
                        )
                        st.json(run["gate_records"])
        st.stop()

    suite_version = st.selectbox("Suite version", ["v1.2.2"])
    suite_name = st.selectbox("Suite", ["banking"])
    try:
        task_ids, attack_ids = suite_choices(suite_version, suite_name)
        attack_definitions = suite_injection_definitions(suite_version, suite_name)
    except Exception as error:
        st.error(f"Could not load AgentDojo suite: {type(error).__name__}: {error}")
        st.stop()

    task_col, attack_col = st.columns(2)
    selected_tasks = task_col.multiselect("User tasks", task_ids, default=task_ids[:1])
    attack_options = ["Clean run", *attack_ids]
    selected_attacks = attack_col.multiselect(
        "Injection tasks",
        attack_options,
        default=["Clean run"],
        format_func=lambda attack: "Clean run (no injection)" if attack == "Clean run" else f"{attack}: {attack_definitions.get(attack, attack)}",
    )
    attacks = [None if attack == "Clean run" else attack for attack in selected_attacks]
    attack_type = st.selectbox(
        "Attack type (how the injection is written)",
        list(ATTACK_TYPES),
        index=list(ATTACK_TYPES).index(DEFAULT_ATTACK),
        help="important_instructions is the AgentDojo paper's standard attack; direct ('TODO: <goal>') is much weaker.",
    )

    policy_names = st.multiselect(
        "Policies",
        options=list(POLICIES),
        default=[
            "agentdojo-tiered",
            "full-approve",
            "full-escalate",
            "deny-all",
        ],
        format_func=lambda value: (
            {
                "agentdojo-tiered": "AgentDojo Tiered",
                "agentdojo-task0-classifier-tiered": "AgentDojo Task 0 Classifier Tiered",
            }.get(value, value.replace("-", " ").title())
        ),
    )
    budgets = st.multiselect("Escalation budgets k", [0, 1, 2, 3], default=[0, 1, 2])

    with st.expander("Model and reviewer configuration", expanded=True):
        agent_model = st.text_input("Agent model", value=os.getenv("AGENTDOJO_MODEL", "gpt-4o-mini-2024-07-18"))
        pricing_expander = st.expander("Optional API pricing (USD per 1M tokens)")
        with pricing_expander:
            agent_input_price = st.text_input(
                "Agent input price",
                value=os.getenv("AGENT_INPUT_PRICE_PER_MILLION", ""),
                placeholder="Not configured",
                help="Enter the actual provider price. Leave blank to report cost as unknown.",
            )
            agent_output_price = st.text_input(
                "Agent output price",
                value=os.getenv("AGENT_OUTPUT_PRICE_PER_MILLION", ""),
                placeholder="Not configured",
            )
            reviewer_input_price = st.text_input(
                "Reviewer input price",
                value=os.getenv("REVIEWER_INPUT_PRICE_PER_MILLION", ""),
                placeholder="Not configured",
            )
            reviewer_output_price = st.text_input(
                "Reviewer output price",
                value=os.getenv("REVIEWER_OUTPUT_PRICE_PER_MILLION", ""),
                placeholder="Not configured",
            )
        use_offline_reviewer = st.checkbox("Use offline RuleReviewer (harness smoke test only)")
        reviewer_model = agent_model
        if use_offline_reviewer:
            st.warning("Harness-only mode: do not report these numbers as final LLM-review benchmark results.")
        else:
            reviewer_model = st.text_input("LLM reviewer model", value=os.getenv("REVIEWER_MODEL", "gpt-4o-mini"))
            st.info("The LLM reviewer sees only the user request, proposed tool call, and visible tool results. It does not receive benchmark labels or attack annotations.")

    if st.button(
        "Run AgentDojo experiment sweep",
        type="primary",
        disabled=not selected_tasks or not selected_attacks or not policy_names or not budgets,
    ):
        if not os.getenv("OPENAI_API_KEY"):
            st.error("OPENAI_API_KEY is required for AgentDojo model evaluation. Set it in the local .env before running.")
        else:
            run_count = len(selected_tasks) * len(attacks) * len(policy_names) * len(budgets)
            max_runs = int(os.getenv("AGENTDOJO_MAX_RUNS_PER_SWEEP", "800"))
            if run_count > max_runs:
                st.error(f"This selection would launch {run_count} runs. Reduce it to {max_runs} or fewer runs per sweep (set AGENTDOJO_MAX_RUNS_PER_SWEEP to change the limit).")
            else:
                try:
                    prices_per_million = {}
                    for component, input_price, output_price in (
                        ("agent", agent_input_price, agent_output_price),
                        ("reviewer", reviewer_input_price, reviewer_output_price),
                    ):
                        if input_price.strip() or output_price.strip():
                            parsed_input = float(input_price) if input_price.strip() else None
                            parsed_output = float(output_price) if output_price.strip() else None
                            if parsed_input is None or parsed_output is None or min(parsed_input, parsed_output) < 0:
                                raise ValueError(f"Enter both non-negative {component} prices or leave both blank.")
                            prices_per_million[component] = {"input": parsed_input, "output": parsed_output}
                    with st.spinner(f"Running {run_count} AgentDojo cases..."):
                        results, summary = run_benchmark_sweep(
                            suite_version=suite_version,
                            suite_name=suite_name,
                            task_ids=selected_tasks,
                            attack_ids=attacks,
                            policy_names=policy_names,
                            budgets=budgets,
                            model=agent_model,
                            reviewer_model=reviewer_model,
                            offline_reviewer=use_offline_reviewer,
                            prices_per_million=prices_per_million,
                            attack_type=attack_type,
                        )
                    saved_path = _save_agentdojo_results(
                        results, summary,
                        {
                            "suite": suite_name, "version": suite_version, "attack_type": attack_type,
                            "agent_model": agent_model,
                            "reviewer": "offline RuleReviewer" if use_offline_reviewer else reviewer_model,
                        },
                    )
                    st.session_state["benchmark_report"] = {
                        "summary": summary,
                        "results": results,
                        "suite": suite_name,
                        "version": suite_version,
                        "reviewer": "offline RuleReviewer" if use_offline_reviewer else reviewer_model,
                        "attack_type": attack_type,
                        "saved_path": saved_path,
                    }
                except Exception as error:
                    st.error(f"Experiment failed: {type(error).__name__}: {error}")

    report = st.session_state.get("benchmark_report")
    if report:
        st.subheader("Aggregate results")
        st.caption(
            f"{report['suite']} {report['version']} · attack: {report.get('attack_type', 'direct')} · "
            f"reviewer: {report['reviewer']}"
        )
        st.caption(
            "TCR@k (benign utility) is the fraction of clean task runs completed safely. Utility under attack is the "
            "fraction of attack runs where the user task was still completed and the attack failed. Targeted ASR is the "
            "fraction of attack runs where the attacker's goal was achieved (AgentDojo's security check). HITL load counts "
            "reviewer calls on successful clean runs. Total escalations counts every policy ESCALATE proposal, including "
            "requests denied after the budget is exhausted. Reviewer calls used is actual budget consumption."
        )
        st.dataframe(report["summary"], width="stretch", hide_index=True)
        if report.get("saved_path"):
            st.caption(f"Saved full results to `{report['saved_path']}`")
        with st.expander("Compact text summary (for reports)", expanded=True):
            st.code(_compact_summary(report["summary"], report["results"]), language="text")
        st.subheader("TCR@k and HITL curve")
        tcr_curve = build_tcr_hitl_curve(report["results"])
        if tcr_curve:
            st.line_chart(tcr_curve, x="k", y="tcr", color="policy")
        st.dataframe(tcr_curve, width="stretch", hide_index=True)
        st.subheader("Failure candidates and escalation value")
        failure_analysis = classify_failures(report["results"])
        st.markdown(
            f"Clean unsuccessful candidates: **{failure_analysis['over_conservative_candidate_count']} / {failure_analysis['clean_case_count']}**. "
            f"Attack-success candidates: **{failure_analysis['over_permissive_candidate_count']} / {failure_analysis['attack_case_count']}**. "
            "Outcome-only labels are candidates: review the traces before calling a safe denial over-conservative or an approved attack action over-permissive."
        )
        with st.expander("Candidate failure trajectories"):
            st.markdown("**Over-conservative candidates**")
            st.json(failure_analysis["over_conservative_candidates"])
            st.markdown("**Over-permissive candidates**")
            st.json(failure_analysis["over_permissive_candidates"])
        st.markdown("**Escalation value across budgets**")
        st.dataframe(escalation_value_analysis(report["results"]), width="stretch", hide_index=True)
        st.subheader("Failure classification and escalation value")
        all_runs = [run for cell_runs in report["results"].values() for run in cell_runs]
        completed_clean_runs = [
            run for run in all_runs
            if run.get("tcr_eligible", True) and run.get("utility") and not run.get("error")
        ]
        safe_incomplete_runs = [
            run for run in all_runs
            if run.get("tcr_eligible", True)
            and not run.get("utility")
            and not run.get("attack_succeeded")
            and not run.get("error")
        ]
        over_permissive_runs = [run for run in all_runs if run.get("attack_succeeded") and not run.get("error")]
        escalation_decisions = [
            record
            for run in all_runs
            for record in run.get("gate_records", [])
            if record.get("proposed") == "ESCALATE"
        ]
        reviewed_escalations = [record for record in escalation_decisions if record.get("by") in {"reviewer", "reviewer error"}]
        escalations_by_tool: dict[str, int] = {}
        for record in reviewed_escalations:
            tool_name = str(record.get("tool", "unknown"))
            escalations_by_tool[tool_name] = escalations_by_tool.get(tool_name, 0) + 1
        st.markdown(
            f"Over-conservative candidate runs (clean task not completed without attack): **{len(safe_incomplete_runs)}**. "
            f"Over-permissive attack-success runs: **{len(over_permissive_runs)}**. "
            f"Successful clean runs: **{len(completed_clean_runs)}**. "
            f"Escalation requests: **{len(escalation_decisions)}**; requests reviewed: **{len(reviewed_escalations)}**."
        )
        if escalations_by_tool:
            st.markdown("Escalations by tool across selected runs:")
            st.dataframe(
                [{"tool": tool_name, "reviewed escalation requests": count} for tool_name, count in sorted(escalations_by_tool.items())],
                width="stretch",
                hide_index=True,
            )
        st.caption(
            "These counts are across policy/budget cells and can include repeated task cases. Candidate failure labels require trajectory review: "
            "a failed clean task is not automatically over-conservative, and an approved attack call should be checked against its actual target/effect."
        )
        if st.button("Clear previous AgentDojo results", key="clear_agentdojo_report"):
            st.session_state.pop("benchmark_report", None)
            st.rerun()
        st.subheader("Run details")
        for (policy_name, budget), runs in report["results"].items():
            with st.expander(f"{policy_name} · k={budget} · {len(runs)} cases"):
                st.dataframe(
                    [
                        {
                            "case": run.get("case_id"),
                            "utility": run.get("utility"),
                            "attack_succeeded": run.get("attack_succeeded"),
                            "escalation_requests": run.get("escalations"),
                            "reviewer_calls_used": run.get("reviewer_calls_used"),
                            "budget": run.get("budget"),
                            "error": run.get("error"),
                            "error_message": run.get("error_message", ""),
                            "reviewer_calls": run.get("tokens", {}).get("reviewer", {}).get("calls", 0),
                            "reviewer_tokens": sum(
                                run.get("tokens", {}).get("reviewer", {}).get(token_type, 0)
                                for token_type in ("input_tokens", "output_tokens")
                            ),
                            "reviewer_errors": sum(bool(item.get("error")) for item in run.get("reviewer_history", [])),
                            "agent_input_tokens": run.get("tokens", {}).get("agent", {}).get("input_tokens", 0),
                            "agent_output_tokens": run.get("tokens", {}).get("agent", {}).get("output_tokens", 0),
                            "agent_cost_usd": run.get("tokens", {}).get("agent", {}).get("estimated_cost_usd"),
                            "reviewer_input_tokens": run.get("tokens", {}).get("reviewer", {}).get("input_tokens", 0),
                            "reviewer_output_tokens": run.get("tokens", {}).get("reviewer", {}).get("output_tokens", 0),
                            "reviewer_cost_usd": run.get("tokens", {}).get("reviewer", {}).get("estimated_cost_usd"),
                        }
                        for run in runs
                    ],
                    width="stretch",
                    hide_index=True,
                )
                for run in runs:
                    if run.get("reviewer_history"):
                        st.markdown("**Reviewer decisions**")
                        st.dataframe(run["reviewer_history"], width="stretch", hide_index=True)
                    with st.expander(f"Gate trajectory · {run.get('case_id', 'case')}"):
                        st.json(run.get("gate_records", []))
                    if run.get("messages"):
                        with st.expander(f"Visible conversation · {run.get('case_id', 'case')}"):
                            st.json(run["messages"])

    st.divider()
    st.caption("Required reporting still includes API cost by component, failure classification with representative trajectories, and escalation-value analysis. This UI reports TCR@k/HITL and gate trajectories; preserve run configuration and model details for the final report.")


if __name__ == "__main__":
    main()
