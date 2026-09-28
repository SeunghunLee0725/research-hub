import pytest

from hub.services.pipeline import (RETRY_LIMIT, StepOutcome, next_kind, retry_decision,
                                   validate_result)


def test_plan_goes_to_implement_when_needed():
    assert next_kind("plan", {"needs_implement": True, "run": None}, {}) == "implement"


def test_plan_goes_to_run_when_it_already_has_a_command():
    run = {"command": "python eval.py", "timeout_hours": 1}
    assert next_kind("plan", {"needs_implement": False, "run": run}, {}) == "run"


def test_plan_without_implement_or_run_goes_to_analyze():
    assert next_kind("plan", {"needs_implement": False, "run": None}, {}) == "analyze"


def test_implement_uses_its_own_run_spec_or_the_plan_one():
    run = {"command": "x", "timeout_hours": 1}
    assert next_kind("implement", {"run": run}, {}) == "run"
    assert next_kind("implement", {"run": None}, {"plan": {"run": run}}) == "run"
    assert next_kind("implement", {"run": None}, {"plan": {"run": None}}) == "analyze"


def test_run_then_analyze_then_verify_then_report_then_done():
    assert next_kind("run", {}, {}) == "analyze"
    assert next_kind("analyze", {}, {}) == "verify"
    assert next_kind("verify", {}, {}) == "report"
    assert next_kind("report", {}, {}) is None


def test_validate_plan_result():
    ok = validate_result("plan", {"summary": "s", "approach": ["a"], "needs_implement": False,
                                  "run": {"command": "python e.py", "timeout_hours": 2}})
    assert ok["run"]["timeout_hours"] == 2
    with pytest.raises(ValueError):
        validate_result("plan", {"summary": "s"})
    with pytest.raises(ValueError):
        validate_result("plan", {"summary": "s", "approach": [], "needs_implement": False,
                                 "run": {"command": "x", "timeout_hours": 1000}})


def test_validate_report_result_card():
    card = {"conclusion": "c", "what_we_did": "w",
            "metrics": [{"name": "recall@5", "baseline": "0.90", "result": "0.93", "note": ""}],
            "trust": {"verified": False, "notes": ["자체 검증만"]}, "limits": ["l"],
            "next_options": [{"title": "t", "why": "y", "est_hours": 2}]}
    assert validate_result("report", {"result_card": card})["result_card"]["conclusion"] == "c"
    with pytest.raises(ValueError):
        validate_result("report", {"result_card": {**card, "next_options": []}})


def test_retry_decision():
    assert retry_decision("timeout", attempt=1, kind="plan") == StepOutcome.RETRY
    assert retry_decision("timeout", attempt=RETRY_LIMIT, kind="plan") == StepOutcome.PROBLEM
    assert retry_decision("session_limit", attempt=5) == StepOutcome.WAIT
    assert retry_decision("auth", attempt=1) == StepOutcome.PROBLEM
    assert retry_decision("refusal", attempt=1) == StepOutcome.PROBLEM
    assert retry_decision("exit_nonzero", attempt=1, kind="run") == StepOutcome.PROBLEM


def test_validation_error_names_the_field():
    with pytest.raises(ValueError, match=r"result_card\.next_options"):
        validate_result("report", {"result_card": {"conclusion": "c", "what_we_did": "w",
                                                   "trust": {"verified": False}, "next_options": []}})


def test_verify_accepts_long_claims():
    long = "가" * 1500
    assert validate_result("verify", {"verified": True, "checks": [{"claim": long, "recomputed": long, "match": True}]})
