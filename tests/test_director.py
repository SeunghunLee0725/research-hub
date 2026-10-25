"""프로젝트 디렉터: 결과 관문마다 헌장 안에서 다음 방향을 정하고, 정해진 경우에만 연구자에게 묻는다."""
from datetime import timedelta

import pytest

from hub.db.models import DirectorDecision, Event, Node, Project, Step, Task
from hub.services import director, leasing, scheduler, tasks
from hub.services.director_prompts import DIRECTION_PATH
from tests.conftest import NOW

PLAN = {"summary": "s", "approach": ["a"], "needs_implement": False,
        "run": {"command": "echo hi", "timeout_hours": 1}}
CARD = {"conclusion": "기준선 대비 +3%p", "what_we_did": "했다", "limits": [],
        "trust": {"verified": True, "notes": []},
        "next_options": [{"title": "다음 A", "why": "필요", "est_hours": 1.0},
                         {"title": "같은 점검 반복", "why": "재확인", "est_hours": 1.0}]}
VERIFY = {"verified": True, "checks": [{"claim": "c", "recomputed": "c", "match": True}], "notes": []}
CHARTER = {"question": "X선 연계가 검출률을 올리는가", "deliverables": ["최종 보고서"], "boundaries": ["원자료 수정 금지"],
           "milestones": [{"id": "M1", "title": "기준선", "done_when": "기준선 recall 측정", "status": "done"},
                          {"id": "M2", "title": "연계 효과", "done_when": "+2%p 이상 재현", "status": "open"}],
           "budget": {"max_tasks": 5, "max_run_hours": None, "deadline": None}}
META = {"_meta": {"model": "claude-opus-5-5"}}


@pytest.fixture()
def world(db):
    node = Node(name="spark-dbb1", token_hash="a" * 64, labels=["claude", "codex"])
    project = Project(slug="drug", name="마약탐지", workdir="/w", node_selector=["node:spark-dbb1"],
                      director_enabled=True)
    db.add_all([node, project])
    db.commit()
    task = tasks.create_task(db, project, "판독 기준 재검토", "o", None)
    tasks.approve_start(db, task, NOW)
    return node, project, task


def _through_report(db, node):
    scheduler.tick(db, NOW)
    for result in (PLAN, {"exit_code": 0, "duration_s": 1}, {"summary": "s", "findings": []},
                   VERIFY, {"summary": "s", "result_card": CARD}):
        step = leasing.lease_step(db, node, NOW)
        leasing.complete_step(db, step, node, "succeeded", {**result, **META}, None, NOW)
        scheduler.tick(db, NOW)
    scheduler.tick(db, NOW)


def _finish_gate_step(db, node, kind, result):
    step = leasing.lease_step(db, node, NOW)
    assert step is not None and step.kind == kind
    leasing.complete_step(db, step, node, "succeeded", {**result, **META}, None, NOW)
    scheduler.tick(db, NOW)
    return step


def _confirmed(db, project, charter=CHARTER):
    director.confirm_charter(db, project, charter, NOW)


def _direct(**extra):
    return {"decision": "approved", "reason": "결과가 M2 에 직접 기여", "action": "continue", "next_option": 0,
            "journal": "M2 를 향해 A 를 고른다", **extra}


def _pending(db, task):
    return db.query(DirectorDecision).filter(DirectorDecision.task_id == task.id,
                                             DirectorDecision.status == "pending").one()


def test_without_a_charter_the_director_drafts_one_and_waits(db, world):
    node, project, task = world
    _through_report(db, node)

    step = _finish_gate_step(db, node, "charter", {"charter": CHARTER, "summary": "기준선은 끝났다"})
    scheduler.tick(db, NOW)

    db.refresh(task), db.refresh(project)
    assert task.status == "review"
    assert project.charter["question"] == CHARTER["question"] and project.charter_confirmed_at is None
    row = _pending(db, task)
    assert row.action == "charter" and row.step_id == step.id and row.model == "claude/claude-opus-5-5"
    assert leasing.lease_step(db, node, NOW) is None  # 연구자 확인 전에는 아무것도 돌지 않는다


def test_confirming_the_charter_lets_the_director_decide(db, world):
    node, project, task = world
    _through_report(db, node)
    _finish_gate_step(db, node, "charter", {"charter": CHARTER, "summary": "s"})
    _confirmed(db, project)
    scheduler.tick(db, NOW)

    step = leasing.lease_step(db, node, NOW)
    assert step.kind == "direct"
    assert CHARTER["question"] in step.input["prompt"] and "작업 1/5개" in step.input["prompt"]
    assert DIRECTION_PATH in step.input["files"]


def test_continue_starts_the_chosen_option_and_updates_milestones(db, world):
    node, project, task = world
    _confirmed(db, project)
    _through_report(db, node)
    _finish_gate_step(db, node, "direct", _direct(milestones=[{"id": "M2", "status": "done", "note": "+3%p"}]))

    db.refresh(task), db.refresh(project)
    assert task.status == "done"
    follow = db.query(Task).filter(Task.parent_task_id == task.id).one()
    assert follow.title == "다음 A" and follow.status == "approved"
    assert [m["status"] for m in project.charter["milestones"]] == ["done", "done"]
    row = db.query(DirectorDecision).filter(DirectorDecision.task_id == task.id).one()
    assert row.action == "continue" and row.status == "applied" and row.payload["progress"] is True


def test_new_task_is_written_by_the_director(db, world):
    node, project, task = world
    _confirmed(db, project)
    _through_report(db, node)
    new = {"title": "연계 효과를 다른 시드로 재현", "objective": "시드 5개로 재현", "success_criteria": "+2%p 이상"}
    _finish_gate_step(db, node, "direct", _direct(action="new_task", next_option=None, new_task=new))

    follow = db.query(Task).filter(Task.parent_task_id == task.id).one()
    assert (follow.title, follow.objective, follow.success_criteria) == (new["title"], new["objective"], "+2%p 이상")
    assert follow.status == "approved"


def test_the_budget_is_enforced_by_the_hub(db, world):
    node, project, task = world
    _confirmed(db, project, {**CHARTER, "budget": {"max_tasks": 1}})
    _through_report(db, node)
    _finish_gate_step(db, node, "direct", _direct())

    db.refresh(task)
    assert task.status == "review"
    row = _pending(db, task)
    assert row.action == "budget" and "작업 1/1개" in row.payload["question"]

    director.answer(db, row, "extend", "두 개만 더", NOW, {"tasks": 2})
    db.refresh(task), db.refresh(project)
    assert task.status == "done" and project.charter["budget"]["max_tasks"] == 3
    assert db.query(Task).filter(Task.parent_task_id == task.id).one().title == "다음 A"


def test_a_conflict_is_asked_and_the_answer_goes_back_to_the_director(db, world):
    node, project, task = world
    _confirmed(db, project)
    _through_report(db, node)
    ask = {"question": "M1 기준선이 이번 결과와 어긋난다", "options": ["기준선 재측정", "이번 결과 폐기"]}
    _finish_gate_step(db, node, "direct", _direct(action="ask", next_option=None, ask=ask))

    row = _pending(db, task)
    assert row.action == "ask"
    director.answer(db, row, "기준선 재측정", "시드 고정해서", NOW)
    scheduler.tick(db, NOW)

    step = leasing.lease_step(db, node, NOW)
    assert step.kind == "direct" and "기준선 재측정 — 시드 고정해서" in step.input["prompt"]


def test_wrap_up_closes_the_project_only_when_the_researcher_agrees(db, world):
    node, project, task = world
    _confirmed(db, project)
    _through_report(db, node)
    _finish_gate_step(db, node, "direct", _direct(action="wrap_up", next_option=None, wrap_up="M1·M2 완료, 보고서 /w/report.md"))

    row = _pending(db, task)
    assert row.action == "wrap_up"
    db.refresh(project)
    assert project.closed_at is None

    director.answer(db, row, "close", "", NOW)
    db.refresh(task), db.refresh(project)
    assert task.status == "done" and project.closed_at == NOW
    assert "보고서" in project.closed_note


def test_a_hold_waits_for_the_researchers_result_approval(db, world):
    node, project, task = world
    _confirmed(db, project)
    _through_report(db, node)
    _finish_gate_step(db, node, "direct", _direct(decision="hold"))
    scheduler.tick(db, NOW)

    assert _pending(db, task).action == "hold"
    assert leasing.lease_step(db, node, NOW) is None
    tasks.approve_result(db, task, None, NOW)
    assert director.pending_decision(db, task) is None


def test_a_failed_director_step_switches_model_then_holds(db, world):
    node, project, task = world
    _confirmed(db, project)
    _through_report(db, node)
    step = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, step, node, "failed", {}, "auth", NOW)
    scheduler.tick(db, NOW)
    retry = leasing.lease_step(db, node, NOW)
    assert retry.kind == "direct" and retry.model == "codex"

    for _ in range(3):
        leasing.complete_step(db, retry, node, "failed", {}, "no_result", NOW)
        scheduler.tick(db, NOW)
        retry = leasing.lease_step(db, node, NOW)
        if retry is None:
            break
    assert _pending(db, task).action == "hold"


def test_stalling_is_counted_and_written_into_the_prompt(db, world):
    _, project, task = world
    _confirmed(db, project)
    for _ in range(5):
        db.add(DirectorDecision(project_id=project.id, task_id=task.id, action="continue", status="applied",
                                payload={"progress": False, "journal": "또 점검"}))
    db.commit()
    assert director.stall_count(db, project) == 5
    db.add(DirectorDecision(project_id=project.id, task_id=task.id, action="new_task", status="applied",
                            payload={"progress": True}))
    db.commit()
    assert director.stall_count(db, project) == 0


def test_every_step_of_a_director_project_reads_the_direction(db, world):
    node, project, task = world
    _confirmed(db, project)
    scheduler.tick(db, NOW)
    plan = leasing.lease_step(db, node, NOW)
    text = plan.input["files"][DIRECTION_PATH]
    assert "X선 연계가 검출률을 올리는가" in text and "[x] M1" in text and "원자료 수정 금지" in text
    assert "direction.md" in plan.input["prompt"]


def test_projects_without_the_director_keep_the_approve_step(db, world):
    node, project, task = world
    project.director_enabled = False
    db.commit()
    _through_report(db, node)
    assert leasing.lease_step(db, node, NOW).kind == "approve"


def test_kickoff_hands_an_idle_project_to_the_director(db, world):
    _, project, task = world
    tasks.cancel(db, task, NOW)
    kick = director.kickoff(db, project)
    assert kick.title == director.KICKOFF_TITLE and kick.status == "proposed"


def test_run_hours_and_deadline_count_against_the_budget(db, world):
    node, project, task = world
    _confirmed(db, project, {**CHARTER, "budget": {"max_tasks": 50, "max_run_hours": 1, "deadline": "2026-09-27"}})
    db.add(Step(task_id=task.id, seq=9, kind="run", model="none", status="succeeded",
                started_at=NOW, ended_at=NOW + timedelta(hours=2)))
    db.commit()
    over = director.budget_usage(db, project, NOW)["over"]
    assert any("머신 실행" in o for o in over) and any("마감" in o for o in over)


def test_unknown_or_reopened_milestones_are_not_progress(db, world):
    node, project, task = world
    _confirmed(db, project)
    _through_report(db, node)
    _finish_gate_step(db, node, "direct", _direct(milestones=[{"id": "M9", "status": "done"},
                                                              {"id": "M1", "status": "open"}]))
    row = db.query(DirectorDecision).filter(DirectorDecision.task_id == task.id).one()
    assert row.payload["progress"] is False


def test_a_decision_that_cannot_be_applied_holds_instead_of_looping(db, world, monkeypatch):
    node, project, task = world
    _confirmed(db, project)
    _through_report(db, node)
    monkeypatch.setattr(director, "_apply_direct", lambda *a: (_ for _ in ()).throw(KeyError("budget")))
    _finish_gate_step(db, node, "direct", _direct())
    scheduler.tick(db, NOW)
    assert _pending(db, task).action == "hold"
    assert db.query(Event).filter(Event.task_id == task.id, Event.level == "error").count() == 1


def test_kickoff_refuses_a_busy_project_and_bad_budget_answers(db, world):
    node, project, task = world
    with pytest.raises(ValueError):
        director.kickoff(db, project)
    _confirmed(db, project, {**CHARTER, "budget": {"max_tasks": 1}})
    _through_report(db, node)
    _finish_gate_step(db, node, "direct", _direct())
    row = _pending(db, task)
    with pytest.raises(ValueError):
        director.answer(db, row, "extend", "", NOW, {"tasks": 0})
    db.rollback()
    with pytest.raises(ValueError):
        director.answer(db, _pending(db, task), "extend", "", NOW, {"tasks": -3})


def test_a_director_gate_does_not_send_the_old_result_alert(db, world):
    from hub.services import notify
    node, project, task = world
    _confirmed(db, project)
    _through_report(db, node)
    sent = []
    notify.deliver(db, NOW, sent.append, "http://hub", 10 ** 9)
    assert not any("결과 도착" in s for s in sent)
