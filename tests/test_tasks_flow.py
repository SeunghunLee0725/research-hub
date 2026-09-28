from datetime import timedelta

import pytest

from hub.db.models import Event, Node, Project, Step, Task
from hub.services import leasing, scheduler, tasks
from tests.conftest import NOW

PLAN_OK = {"summary": "계획", "approach": ["a"], "needs_implement": False,
           "run": {"command": "python eval.py", "timeout_hours": 1}}
CARD = {"conclusion": "c", "what_we_did": "w", "metrics": [], "trust": {"verified": False, "notes": []},
        "limits": [], "next_options": [{"title": "t", "why": "y", "est_hours": 1}]}


@pytest.fixture()
def world(db):
    dbb1 = Node(name="spark-dbb1", token_hash="a" * 64, labels=["gpu", "claude", "codex"])
    other = Node(name="spark-2588", token_hash="b" * 64, labels=["gpu", "claude", "codex"])
    project = Project(slug="plasma-kg", name="플라즈마 KG", workdir="/w", node_selector=["node:spark-dbb1"])
    db.add_all([dbb1, other, project])
    db.commit()
    return dbb1, other, project


def _approved_task(db, project):
    task = tasks.create_task(db, project, "검색 회귀 조사", "recall 회복", "recall >= 0.9")
    tasks.approve_start(db, task, NOW)
    return task


def test_create_task_opens_start_approval(db, world):
    _, _, project = world
    task = tasks.create_task(db, project, "t", "o", None)
    assert task.status == "proposed"
    assert tasks.pending_approval(db, task).kind == "start"


def test_approve_start_moves_to_approved(db, world):
    task = _approved_task(db, world[2])
    assert task.status == "approved" and tasks.pending_approval(db, task) is None


def test_scheduler_creates_plan_step_and_node_selector_is_respected(db, world):
    dbb1, other, project = world
    task = _approved_task(db, project)
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert task.status == "running"
    assert leasing.lease_step(db, other, NOW) is None
    step = leasing.lease_step(db, dbb1, NOW)
    assert step.kind == "plan" and step.model == "claude" and step.status == "leased"
    assert "검색 회귀 조사" in step.input["prompt"]
    assert step.input["result_path"].endswith(f"/.research-hub/steps/{step.id}/result.json")
    assert leasing.lease_step(db, dbb1, NOW) is None


def test_full_pipeline_to_review(db, world):
    dbb1, _, project = world
    task = _approved_task(db, project)
    results = {"plan": PLAN_OK, "run": {"exit_code": 0, "duration_s": 5, "log_tail": "ok"},
               "analyze": {"summary": "분석", "findings": ["f"], "criteria_met": True},
               "report": {"result_card": CARD}}
    seen = []
    for _ in range(6):
        scheduler.tick(db, NOW)
        step = leasing.lease_step(db, dbb1, NOW)
        if step is None:
            break
        seen.append(step.kind)
        leasing.complete_step(db, step, dbb1, "succeeded", results[step.kind], None, NOW)
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert seen == ["plan", "run", "analyze", "report"]
    assert task.status == "review" and task.result_card["conclusion"] == "c"
    assert tasks.pending_approval(db, task).kind == "result"
    run_step = db.query(Step).filter_by(task_id=task.id, kind="run").one()
    assert run_step.model == "none" and run_step.input["command"] == "python eval.py"


def test_invalid_result_is_retried_then_problem(db, world):
    dbb1, _, project = world
    task = _approved_task(db, project)
    for attempt in range(1, 4):
        scheduler.tick(db, NOW)
        step = leasing.lease_step(db, dbb1, NOW)
        assert step.attempt == attempt
        leasing.complete_step(db, step, dbb1, "succeeded", {"summary": "missing fields"}, None, NOW)
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert task.status == "problem"
    assert db.query(Event).filter_by(task_id=task.id, level="error").count() >= 1


def test_session_limit_waits_without_consuming_attempts(db, world):
    dbb1, _, project = world
    _approved_task(db, project)
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, dbb1, NOW)
    leasing.complete_step(db, step, dbb1, "failed", None, "session_limit", NOW)
    scheduler.tick(db, NOW)
    assert leasing.lease_step(db, dbb1, NOW + timedelta(minutes=5)) is None
    retry = leasing.lease_step(db, dbb1, NOW + timedelta(minutes=31))
    assert retry.kind == "plan" and retry.attempt == 1


def test_expired_lease_is_lost_and_retried(db, world):
    dbb1, _, project = world
    _approved_task(db, project)
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, dbb1, NOW)
    later = NOW + leasing.LEASE + timedelta(seconds=1)
    scheduler.tick(db, later)
    db.refresh(step)
    assert step.status == "lost"
    assert leasing.lease_step(db, dbb1, later).attempt == 2


def test_progress_extends_lease_and_logs_event(db, world):
    dbb1, _, project = world
    task = _approved_task(db, project)
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, dbb1, NOW)
    later = NOW + timedelta(minutes=8)
    leasing.record_progress(db, step, dbb1, "epoch 3/10", later)
    db.refresh(step)
    assert step.status == "running" and step.lease_until == later + leasing.LEASE
    assert db.query(Event).filter_by(task_id=task.id, message="epoch 3/10").count() == 1


def test_other_node_cannot_report_on_step(db, world):
    dbb1, other, project = world
    _approved_task(db, project)
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, dbb1, NOW)
    with pytest.raises(leasing.NotYourStep):
        leasing.complete_step(db, step, other, "succeeded", PLAN_OK, None, NOW)


def test_task_is_pinned_to_first_node(db, world):
    dbb1, other, _ = world
    project = Project(slug="free", name="Free", workdir="/w", node_selector=["gpu"])
    db.add(project)
    db.commit()
    task = _approved_task(db, project)
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, other, NOW)
    leasing.complete_step(db, step, other, "succeeded", PLAN_OK, None, NOW)
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert task.node_id == other.id
    assert leasing.lease_step(db, dbb1, NOW) is None
    assert leasing.lease_step(db, other, NOW).kind == "run"
