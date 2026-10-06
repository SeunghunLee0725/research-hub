"""승인 게이트도 에이전트가 처리한다. 누가 결정했는지는 기록에 남는다."""
import pytest

from hub.db.models import Approval, Event, Node, Project, Step, Task
from hub.services import leasing, scheduler, tasks
from tests.conftest import NOW

PLAN = {"summary": "s", "approach": ["a"], "needs_implement": False,
        "run": {"command": "echo hi", "timeout_hours": 1}}
CARD = {"conclusion": "끝났다", "what_we_did": "했다", "numbers": [], "limits": [],
        "trust": {"verified": True, "notes": []},
        "next_options": [{"title": "다음 A", "why": "필요", "est_hours": 1.0},
                         {"title": "다음 B", "why": "선택", "est_hours": 2.0}]}
VERIFY = {"verified": True, "checks": [{"claim": "c", "recomputed": "c", "match": True}], "notes": []}


@pytest.fixture()
def world(db):
    node = Node(name="spark-dbb1", token_hash="a" * 64, labels=["claude", "codex"])
    project = Project(slug="drug", name="마약탐지", workdir="/w", node_selector=["node:spark-dbb1"])
    db.add_all([node, project])
    db.commit()
    task = tasks.create_task(db, project, "판독 기준 재검토", "o", None)
    tasks.approve_start(db, task, NOW)
    return node, project, task


def _through_report(db, node, card=CARD, verify=VERIFY):
    """plan → run → analyze → verify → report, leaving the task at its result gate."""
    scheduler.tick(db, NOW)
    for result in (PLAN, {"exit_code": 0, "duration_s": 1}, {"summary": "s", "findings": []},
                   verify, {"summary": "s", "result_card": card}):
        step = leasing.lease_step(db, node, NOW)
        leasing.complete_step(db, step, node, "succeeded", {**result, "_meta": {"model": "claude-opus-5-5"}},
                              None, NOW)
        scheduler.tick(db, NOW)
    scheduler.tick(db, NOW)  # the gate is opened by the report tick; the approve step comes on the next one
    return step


def test_projects_let_the_agent_decide_approvals_by_default(db, world):
    _, project, _ = world
    assert project.auto_approve is True


def test_the_agent_approves_the_result_and_the_record_names_the_model(db, world):
    node, _, task = world
    _through_report(db, node)
    db.refresh(task)
    assert task.status == "review"

    approve = leasing.lease_step(db, node, NOW)
    assert approve.kind == "approve"
    leasing.complete_step(db, approve, node, "succeeded",
                          {"decision": "approved", "reason": "검증 통과", "next_option": 0,
                           "_meta": {"model": "claude-opus-5-5"}}, None, NOW)
    scheduler.tick(db, NOW)

    db.refresh(task)
    assert task.status == "done"
    row = db.query(Approval).filter(Approval.task_id == task.id, Approval.kind == "result").one()
    assert row.status == "approved"
    assert row.decided_by == "agent:claude/claude-opus-5-5"
    assert row.choice["option"] == 0
    follow = db.query(Task).filter(Task.parent_task_id == task.id).one()
    assert follow.title == "다음 A" and follow.status == "approved"
    assert any("에이전트" in e.message for e in db.query(Event).filter(Event.task_id == task.id))


def test_the_agent_can_hold_a_result_for_the_researcher(db, world):
    node, _, task = world
    _through_report(db, node)
    approve = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, approve, node, "succeeded",
                          {"decision": "hold", "reason": "수치가 앞 작업과 어긋난다", "next_option": None},
                          None, NOW)
    scheduler.tick(db, NOW)

    db.refresh(task)
    assert task.status == "review"
    row = db.query(Approval).filter(Approval.task_id == task.id, Approval.kind == "result").one()
    assert row.status == "pending" and row.decided_by is None
    assert any("보류" in e.message for e in db.query(Event).filter(Event.task_id == task.id))


def test_a_project_can_keep_approvals_with_the_researcher(db, world):
    node, project, task = world
    project.auto_approve = False
    db.commit()
    _through_report(db, node)
    db.refresh(task)
    assert task.status == "review"
    assert leasing.lease_step(db, node, NOW) is None


def test_a_human_decision_is_recorded_as_human(db, world):
    node, project, task = world
    project.auto_approve = False
    db.commit()
    _through_report(db, node)
    tasks.approve_result(db, task, None, NOW)
    row = db.query(Approval).filter(Approval.task_id == task.id, Approval.kind == "result").one()
    assert row.decided_by == "human"


def test_a_proposed_task_is_started_by_the_agent(db, world):
    node, project, first = world
    tasks.cancel(db, first, NOW)
    task = tasks.create_task(db, project, "새 작업", "o", None)
    assert task.status == "proposed"
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert task.status == "running"
    row = db.query(Approval).filter(Approval.task_id == task.id, Approval.kind == "start").one()
    assert row.status == "approved" and row.decided_by == "agent:auto"
