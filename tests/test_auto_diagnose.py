"""막힌 작업을 에이전트가 스스로 진단하고 고친다. 에이전트에게 맡기지 않은 프로젝트만 연구자가 고른다."""
import re

import pytest
from fastapi.testclient import TestClient

from hub.db.models import Approval, Event, HumanRequest, Node, Project, Step, Task
from hub.main import create_app
from hub.services import human, leasing, scheduler, tasks
from tests.conftest import ADMIN_PASSWORD, NOW

PLAN = {"summary": "s", "approach": ["a"], "needs_implement": False,
        "run": {"command": "bash run.sh", "timeout_hours": 1}}
DIAGNOSIS = {"cause": "pytest 가 시스템 python 에 없어 run 명령이 바로 끝났다",
             "fix": "테스트를 표준 unittest 로 바꾸고 run 명령의 pytest 호출을 교체한다",
             "confidence": "high", "risk": "낮음 — 테스트 실행 방식만 바뀐다", "retry_after_fix": True}


@pytest.fixture()
def world(db):
    node = Node(name="spark-2588", token_hash="a" * 64, labels=["claude", "codex"])
    project = Project(slug="drug", name="마약탐지", workdir="/w", node_selector=["node:spark-2588"])
    db.add_all([node, project])
    db.commit()
    task = tasks.create_task(db, project, "채점", "o", None)
    tasks.approve_start(db, task, NOW)
    return node, project, task


def _break_the_run(db, node, task):
    scheduler.tick(db, NOW)
    plan = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, plan, node, "succeeded", PLAN, None, NOW)
    scheduler.tick(db, NOW)
    run = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, run, node, "failed", {"exit_code": 1, "log_tail": "No module named pytest"},
                          "exit_nonzero", NOW)
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert task.status == "problem"
    return run


def _diagnose(db, node, result=DIAGNOSIS):
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, node, NOW)
    assert step is not None and step.kind == "diagnose"
    leasing.complete_step(db, step, node, "succeeded", {**result, "_meta": {"model": "claude-opus-5-5"}},
                          None, NOW)
    scheduler.tick(db, NOW)
    return step


def test_projects_diagnose_stuck_tasks_by_default(db, world):
    _, project, _ = world
    assert project.auto_diagnose is True


def test_a_stuck_task_gets_a_diagnose_step(db, world):
    node, _, task = world
    _break_the_run(db, node, task)
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, node, NOW)
    assert step is not None and step.kind == "diagnose"
    assert "No module named pytest" in step.input["prompt"]


def _ask_the_researcher(db, project):
    project.auto_approve = False
    db.commit()


def test_the_diagnosis_waits_for_the_researcher_to_choose(db, world):
    node, project, task = world
    _ask_the_researcher(db, project)
    _break_the_run(db, node, task)
    step = _diagnose(db, node)

    db.refresh(task)
    assert task.status == "problem"  # 고치기 전에는 멈춘 그대로다
    row = db.query(Approval).filter(Approval.kind == "fix").one()
    assert row.status == "pending"
    assert row.choice["cause"].startswith("pytest")
    assert row.choice["diagnosed_by"] == "agent:claude/claude-opus-5-5"
    assert any("원인" in e.message for e in db.query(Event).filter(Event.task_id == task.id))
    assert db.query(Step).filter(Step.task_id == task.id, Step.kind == "diagnose").count() == 1


def test_one_diagnosis_per_problem_not_a_loop(db, world):
    node, project, task = world
    _ask_the_researcher(db, project)
    _break_the_run(db, node, task)
    _diagnose(db, node)
    for _ in range(3):
        scheduler.tick(db, NOW)
    assert db.query(Step).filter(Step.task_id == task.id, Step.kind == "diagnose").count() == 1


def test_approving_the_fix_restarts_the_task(settings, db, world):
    node, project, task = world
    _ask_the_researcher(db, project)
    _break_the_run(db, node, task)
    _diagnose(db, node)

    with TestClient(create_app(settings)) as client:
        client.post("/login", data={"password": ADMIN_PASSWORD})
        page = client.get(f"/tasks/{task.id}").text
        assert "원인" in page and "제안대로 고치기" in page
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        assert client.post(f"/tasks/{task.id}/apply-fix", data={"csrf": csrf, "decision": "apply"},
                           follow_redirects=False).status_code == 303

    db.refresh(task)
    assert task.status == "running"
    row = db.query(Approval).filter(Approval.kind == "fix").one()
    assert row.status == "approved" and row.decided_by == "human"
    latest = db.query(Step).filter(Step.task_id == task.id).order_by(Step.id.desc()).first()
    assert DIAGNOSIS["fix"] in latest.input["prompt"]


def test_declining_leaves_the_task_alone(settings, db, world):
    node, project, task = world
    _ask_the_researcher(db, project)
    _break_the_run(db, node, task)
    _diagnose(db, node)

    with TestClient(create_app(settings)) as client:
        client.post("/login", data={"password": ADMIN_PASSWORD})
        page = client.get(f"/tasks/{task.id}").text
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        client.post(f"/tasks/{task.id}/apply-fix", data={"csrf": csrf, "decision": "decline"})

    db.refresh(task)
    assert task.status == "problem"
    row = db.query(Approval).filter(Approval.kind == "fix").one()
    assert row.status == "rejected" and row.decided_by == "human"


def test_a_project_can_turn_diagnosis_off(db, world):
    node, project, task = world
    project.auto_diagnose = False
    db.commit()
    _break_the_run(db, node, task)
    scheduler.tick(db, NOW)
    assert leasing.lease_step(db, node, NOW) is None


FORM = {"instructions": "X선 판독", "answers_path": ".research-hub/answers.jsonl", "requires_human": True,
        "fields": [{"name": "label", "label": "내용물", "type": "choice", "choices": ["drug", "clean"],
                    "required": True}],
        "items": [{"id": "X01", "title": "판독 01", "body": "1"}, {"id": "X02", "title": "판독 02", "body": "2"}]}


def _fail_until_problem(db, node, task, kind):
    db.refresh(task)
    while task.status == "running":
        step = leasing.lease_step(db, node, NOW)
        assert step.kind == kind
        leasing.complete_step(db, step, node, "failed", None, "bad_result", NOW)
        scheduler.tick(db, NOW)
        db.refresh(task)
    assert task.status == "problem"


def _no_tick_errors(db, task):
    return db.query(Event).filter(Event.task_id == task.id, Event.message.contains("단계 진행 실패")).count() == 0


def test_a_fix_for_a_form_the_agent_cannot_fill_hands_it_to_the_person(db, world):
    node, project, task = world
    _ask_the_researcher(db, project)
    scheduler.tick(db, NOW)
    plan = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, plan, node, "succeeded", {**PLAN, "human_input": FORM}, None, NOW)
    scheduler.tick(db, NOW)
    _fail_until_problem(db, node, task, "review")
    _diagnose(db, node)
    tasks.apply_fix(db, task, NOW)
    request = db.query(HumanRequest).one()
    db.refresh(task)
    assert task.status == "waiting_human" and request.answered_by == "human"
    assert leasing.lease_step(db, node, NOW) is None

    human.submit(db, request, {"X01": {"label": "drug"}, "X02": {"label": "clean"}}, NOW)
    scheduler.tick(db, NOW)
    run = leasing.lease_step(db, node, NOW)
    assert run.kind == "run" and '"drug"' in run.input["files"][".research-hub/answers.jsonl"]
    assert _no_tick_errors(db, task)


def test_the_step_rerun_after_a_fix_carries_the_task_on(db, world):
    node, _, task = world
    scheduler.tick(db, NOW)
    _fail_until_problem(db, node, task, "plan")
    _diagnose(db, node)
    rerun = leasing.lease_step(db, node, NOW)
    assert rerun.kind == "plan"
    leasing.complete_step(db, rerun, node, "succeeded", PLAN, None, NOW)
    scheduler.tick(db, NOW)
    assert leasing.lease_step(db, node, NOW).kind == "run"
    assert _no_tick_errors(db, task)


def test_the_agent_applies_a_confident_fix_itself(db, world):
    node, _, task = world
    _break_the_run(db, node, task)
    _diagnose(db, node)

    db.refresh(task)
    assert task.status == "running"
    row = db.query(Approval).filter(Approval.kind == "fix").one()
    assert row.status == "approved" and row.decided_by == "agent:claude/claude-opus-5-5"
    latest = db.query(Step).filter(Step.task_id == task.id).order_by(Step.id.desc()).first()
    assert latest.kind == "implement"
    assert DIAGNOSIS["fix"] in latest.input["prompt"] and "에이전트가 적용" in latest.input["prompt"]
    assert "연구자가 승인했다" not in latest.input["prompt"]


def test_a_low_confidence_fix_waits_for_the_researcher(db, world):
    node, _, task = world
    _break_the_run(db, node, task)
    _diagnose(db, node, {**DIAGNOSIS, "confidence": "low"})

    db.refresh(task)
    assert task.status == "problem"
    assert db.query(Approval).filter(Approval.kind == "fix").one().status == "pending"


def test_the_agent_stops_after_two_fixes_that_did_not_help(db, world):
    node, _, task = world
    scheduler.tick(db, NOW)
    for _ in range(2):
        _fail_until_problem(db, node, task, "plan")
        _diagnose(db, node)
        db.refresh(task)
        assert task.status == "running"
    _fail_until_problem(db, node, task, "plan")
    _diagnose(db, node)

    db.refresh(task)
    assert task.status == "problem"
    rows = db.query(Approval).filter(Approval.kind == "fix").order_by(Approval.id).all()
    assert [r.status for r in rows] == ["approved", "approved", "pending"]
    assert any("연구자 확인" in e.message for e in db.query(Event).filter(Event.task_id == task.id))


def test_the_agent_retries_a_form_it_could_not_fill(db, world):
    node, _, task = world
    scheduler.tick(db, NOW)
    plan = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, plan, node, "succeeded", {**PLAN, "human_input": FORM}, None, NOW)
    scheduler.tick(db, NOW)
    _fail_until_problem(db, node, task, "review")
    _diagnose(db, node)

    db.refresh(task)
    assert task.status == "running"
    rerun = leasing.lease_step(db, node, NOW)
    assert rerun.kind == "review" and DIAGNOSIS["fix"] in rerun.input["prompt"]
    assert _no_tick_errors(db, task)
