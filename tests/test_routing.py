import pytest

from hub.db.models import Node, Project, QuotaSnapshot, Step
from hub.services import leasing, router, scheduler, tasks
from hub.services.pipeline import enforce_trust, next_kind, validate_result
from tests.conftest import NOW

CARD = {"conclusion": "c", "what_we_did": "w", "metrics": [], "trust": {"verified": True, "notes": ["자체 확인"]},
        "limits": [], "next_options": [{"title": "t", "why": "y", "est_hours": 1}]}


def test_preferred_models():
    assert [router.choose_model(k, {}) for k in ("plan", "implement", "run", "analyze", "verify", "report")] == \
        ["claude", "codex", "none", "claude", "codex", "claude"]


def test_quota_pressure_switches_model():
    assert router.choose_model("implement", {"codex": 92.0, "claude": 30.0}) == "claude"
    assert router.choose_model("plan", {"claude": 90.0, "codex": 10.0}) == "codex"
    assert router.choose_model("plan", {"claude": 90.0, "codex": 95.0}) == "claude"
    assert router.choose_model("run", {"claude": 99.0}) == "none"


def test_usage_from_snapshots():
    snaps = {"claude": [{"used_percent": 40}, {"used_percent": 88}], "codex": []}
    assert router.usage_from_windows(snaps) == {"claude": 88, "codex": None}


def test_analyze_goes_to_verify_then_report():
    assert next_kind("analyze", {}, {}) == "verify"
    assert next_kind("verify", {}, {}) == "report"


def test_verify_result_requires_all_checks_to_match():
    ok = validate_result("verify", {"verified": True, "checks": [
        {"claim": "recall 0.93", "recomputed": "0.933", "match": True}], "notes": []})
    assert ok["verified"] is True
    mixed = validate_result("verify", {"verified": True, "checks": [
        {"claim": "a", "recomputed": "1", "match": True}, {"claim": "b", "recomputed": "2", "match": False}]})
    assert mixed["verified"] is False
    empty = validate_result("verify", {"verified": True, "checks": []})
    assert empty["verified"] is False


def test_enforce_trust_uses_verify_step():
    verified = {"verified": True, "checks": [{"claim": "a", "recomputed": "1", "match": True}], "notes": []}
    assert enforce_trust(CARD, verified)["trust"]["verified"] is True
    failed = {"verified": False, "checks": [{"claim": "recall 0.93", "recomputed": "0.90", "match": False}],
              "notes": []}
    card = enforce_trust(CARD, failed)
    assert card["trust"]["verified"] is False
    assert any("recall 0.93" in n for n in card["trust"]["notes"])
    assert enforce_trust(CARD, None)["trust"]["verified"] is False
    assert CARD["trust"]["verified"] is True  # input not mutated


@pytest.fixture()
def world(db):
    node = Node(name="spark-dbb1", token_hash="a" * 64, labels=["gpu", "claude", "codex"])
    project = Project(slug="p", name="P", workdir="/w", node_selector=["node:spark-dbb1"])
    db.add_all([node, project])
    db.commit()
    task = tasks.create_task(db, project, "t", "o", None)
    tasks.approve_start(db, task, NOW)
    return node, task


def _fail(db, node, error_class):
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, step, node, "failed", None, error_class, NOW)
    scheduler.tick(db, NOW)
    return step


def test_refusal_switches_to_other_model_once(db, world):
    node, task = world
    first = _fail(db, node, "refusal")
    assert first.model == "claude"
    second = leasing.lease_step(db, node, NOW)
    assert second.kind == "plan" and second.model == "codex" and second.attempt == 2
    leasing.complete_step(db, second, node, "failed", None, "refusal", NOW)
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert task.status == "problem"


def test_sandbox_error_falls_back(db, world):
    node, task = world
    db.add(QuotaSnapshot(node_id=node.id, provider="claude", at=NOW,
                         windows=[{"id": "five_hour", "label": "5시간", "used_percent": 95}]))
    db.commit()
    first = _fail(db, node, "sandbox")
    assert first.model == "codex"
    assert leasing.lease_step(db, node, NOW).model == "claude"


def test_node_without_codex_label_cannot_take_codex_step(db):
    node = Node(name="n", token_hash="b" * 64, labels=["claude"])
    project = Project(slug="q", name="Q", workdir="/w", node_selector=["node:n"])
    db.add_all([node, project])
    db.commit()
    db.add(QuotaSnapshot(node_id=node.id, provider="claude", at=NOW,
                         windows=[{"id": "five_hour", "label": "5시간", "used_percent": 95}]))
    db.commit()
    task = tasks.create_task(db, project, "t", "o", None)
    tasks.approve_start(db, task, NOW)
    scheduler.tick(db, NOW)
    assert db.query(Step).one().model == "codex"
    assert leasing.lease_step(db, node, NOW) is None
