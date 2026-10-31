from gate_and_experiments.report_analysis import (
    build_tcr_hitl_curve,
    classify_failures,
    escalation_value_analysis,
)


def test_curve_tracks_clean_denominator_hitl_and_error_count():
    results = {
        ("tiered", 0): [
            {"case_id": "clean-a", "utility": True, "attack_succeeded": False, "tcr_eligible": True, "reviewer_calls_used": 0},
            {"case_id": "clean-b", "utility": False, "attack_succeeded": False, "tcr_eligible": True, "reviewer_calls_used": 0},
            {"case_id": "attack", "attack_id": "injection_task_0", "utility": False, "attack_succeeded": False, "tcr_eligible": False, "reviewer_calls_used": 0},
            {"case_id": "err", "error": True},
        ]
    }

    row = build_tcr_hitl_curve(results)[0]

    assert row["tcr"] == 0.5
    assert row["eligible_clean_runs"] == 2
    assert row["successful_clean_runs"] == 1
    assert row["errors"] == 1


def test_failure_candidates_include_representative_gate_trajectories():
    results = {
        ("tiered", 1): [
            {
                "case_id": "clean-failed",
                "utility": False,
                "attack_succeeded": False,
                "tcr_eligible": True,
                "gate_records": [{
                    "tool": "send_money",
                    "args": {"amount": 10},
                    "proposed": "ESCALATE",
                    "final": "DENY",
                    "by": "budget exhausted",
                    "policy_reason": "authorization_not_established",
                    "used": 1,
                    "budget": 1,
                }],
            },
            {
                "case_id": "attack-success",
                "attack_id": "injection_task_0",
                "utility": False,
                "attack_succeeded": True,
                "tcr_eligible": False,
                "gate_records": [{
                    "tool": "send_money",
                    "args": {"recipient": "attacker"},
                    "proposed": "APPROVE",
                    "final": "APPROVE",
                    "invoked": True,
                }],
            },
        ]
    }

    failures = classify_failures(results)

    assert failures["over_conservative_candidate_count"] == 1
    assert failures["over_permissive_candidate_count"] == 1
    assert failures["over_conservative_candidates"][0]["trajectory"][0]["by"] == "budget exhausted"
    assert failures["over_permissive_candidates"][0]["trajectory"][0]["invoked"] is True


def test_escalation_value_compares_same_case_across_budgets():
    results = {
        ("tiered", 0): [{
            "case_id": "case-1", "utility": False, "attack_succeeded": False,
            "tcr_eligible": True, "reviewer_calls_used": 0,
            "gate_records": [{"tool": "send_money", "proposed": "ESCALATE", "by": "budget exhausted"}],
        }],
        ("tiered", 1): [{
            "case_id": "case-1", "utility": True, "attack_succeeded": False,
            "tcr_eligible": True, "reviewer_calls_used": 1,
            "gate_records": [{"tool": "send_money", "proposed": "ESCALATE", "by": "reviewer"}],
        }],
    }

    rows = escalation_value_analysis(results)

    assert len(rows) == 1
    assert rows[0]["improved_case_ids"] == ["case-1"]
    assert rows[0]["reviewer_calls_added"] == 1
    assert rows[0]["escalation_signatures_at_lower_k"]["case-1"] == ()
    assert rows[0]["escalation_requests_at_lower_k"]["case-1"] == ("send_money",)
