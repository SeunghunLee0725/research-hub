import json
import re

import pytest
from fastapi.testclient import TestClient

from hub.db.models import Event, HumanRequest, Node, Project, Step, Task
from hub.main import create_app
from hub.services import human, leasing, scheduler, tasks
from tests.conftest import ADMIN_PASSWORD, NOW

FORM = {"instructions": "논문이 질문에 답하는지 판정", "answers_path": ".research-hub/answers.jsonl",
        "fields": [{"name": "human_label", "label": "판정", "type": "choice",
                    "choices": ["ANSWERS", "PARTIAL", "OFF_TOPIC"]},
                   {"name": "human_reason", "label": "근거", "type": "text"}],
        "items": [{"id": "G003P1", "title": "G003 · 1", "body": "초록 1"},
                  {"id": "G003P2", "title": "G003 · 2", "body": "초록 2"}]}
PLAN = {"summary": "s", "approach": ["a"], "needs_implement": False,
        "run": {"command": "python score.py", "timeout_hours": 1}, "human_input": FORM}
REVIEW_OK = {"summary": "2건 판정", "answers": {"G003P1": {"human_label": "ANSWERS", "human_reason": "기전 제시"},
                                            "G003P2": {"human_label": "OFF_TOPIC"}}}
META = {"model": "claude-opus-5-5"}


@pytest.fixture()
def world(db):
    node = Node(name="spark-dbb1", token_hash="a" * 64, labels=["claude", "codex"])
    project = Project(slug="plasma-kg", name="플라즈마 KG", workdir="/w", node_selector=["node:spark-dbb1"])
    db.add_all([node, project])
    db.commit()
    task = tasks.create_task(db, project, "논문 판정", "o", None)
    tasks.approve_start(db, task, NOW)
    return node, project, task


def _plan(db, node, result=PLAN):
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, step, node, "succeeded", result, None, NOW)
    scheduler.tick(db, NOW)
    return step


def _answer_review(db, node, result=REVIEW_OK):
    review = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, review, node, "succeeded", {**result, "_meta": META}, None, NOW)
    scheduler.tick(db, NOW)
    return review


def test_projects_delegate_forms_to_the_agent_by_default(db, world):
    node, project, task = world
    assert project.auto_ai_review is True
    _plan(db, node)
    db.refresh(task)
    assert task.status == "running"
    assert db.query(HumanRequest).one().answered_by == "llm"


def test_every_form_in_a_task_goes_to_the_agent(db, world):
    node, _, task = world
    _plan(db, node)
    _answer_review(db, node)
    run = leasing.lease_step(db, node, NOW)
    assert run.kind == "run" and '"G003P2"' in run.input["files"][".research-hub/answers.jsonl"]
    leasing.complete_step(db, run, node, "succeeded", {"exit_code": 0, "duration_s": 1}, None, NOW)
    scheduler.tick(db, NOW)
    analyze = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, analyze, node, "succeeded",
                          {"summary": "s", "findings": [],
                           "human_input": {**FORM, "answers_path": "b.jsonl"}}, None, NOW)
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert task.status == "running"
    assert leasing.lease_step(db, node, NOW).kind == "review"


def test_the_answering_model_is_recorded(db, world):
    node, _, task = world
    _plan(db, node)
    _answer_review(db, node)
    request = db.query(HumanRequest).one()
    assert request.answered_by == "llm"
    assert request.answered_by_model == "claude/claude-opus-5-5"
    files = human.submitted_files(db, task)
    record = json.loads(files[".research-hub/answers.jsonl.provenance.json"])
    assert record["submitted_by"] == "llm" and record["answered_by_model"] == "claude/claude-opus-5-5"


def test_a_signature_form_also_goes_to_the_agent(db, world):
    """The researcher asked not to be handed work; the agent signs as itself and that is recorded."""
    node, _, task = world
    _plan(db, node, {**PLAN, "human_input": {**FORM, "requires_human": True}})
    db.refresh(task)
    assert task.status == "running"
    assert db.query(HumanRequest).one().answered_by == "llm"


def test_the_review_prompt_forbids_signing_as_a_person(db, world):
    from hub.db.models import Project
    from hub.services.prompts import build_review_prompt
    project = db.query(Project).one()
    form = {**FORM, "requires_human": True,
            "fields": [{**f, "choices": f.get("choices", [])} for f in FORM["fields"]]}
    prompt = build_review_prompt(project, db.query(Task).first(), form, "f.json", "r.json")
    assert "기록 위조" in prompt and "agent:claude" in prompt


def test_a_form_waiting_for_a_person_can_be_handed_to_the_agent(settings, db, world):
    node, project, task = world
    project.auto_ai_review = False  # this project keeps a person in the loop
    db.commit()
    _plan(db, node, {**PLAN, "human_input": {**FORM, "requires_human": True}})
    db.refresh(task)
    assert task.status == "waiting_human"
    request = db.query(HumanRequest).one()
    with TestClient(create_app(settings)) as client:
        client.post("/login", data={"password": ADMIN_PASSWORD})
        page = client.get(f"/tasks/{task.id}").text
        assert "AI에게 맡기기" in page
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        assert client.post(f"/tasks/{task.id}/delegate-ai", data={"csrf": csrf},
                           follow_redirects=False).status_code == 303
    db.refresh(task)
    db.refresh(request)
    assert task.status == "running" and request.answered_by == "llm"


def test_review_by_human_keeps_the_person_in_the_loop(db, world):
    node, project, first = world
    tasks.cancel(db, first, NOW)
    task = tasks.create_task(db, project, "사람이 볼 작업", "o", None)
    tasks.approve_start(db, task, NOW, review_by="human")
    _plan(db, node)
    db.refresh(task)
    assert task.status == "waiting_human"
    assert db.query(HumanRequest).one().answered_by == "human"


def test_invalid_ai_answers_are_retried(db, world):
    node, _, task = world
    _plan(db, node)
    review = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, review, node, "succeeded",
                          {"summary": "s", "answers": {"G003P1": {"human_label": "MAYBE"}}}, None, NOW)
    scheduler.tick(db, NOW)
    retry = leasing.lease_step(db, node, NOW)
    assert retry.kind == "review" and retry.attempt == 2
    assert db.get(Step, review.id).error_class == "bad_result"


def test_form_file_reaches_the_review_step(db, world):
    node, _, task = world
    _plan(db, node)
    review = leasing.lease_step(db, node, NOW)
    form_file = ".research-hub/steps/%d/form.json" % review.id
    assert json.loads(review.input["files"][form_file])["items"][1]["id"] == "G003P2"


def test_reopen_after_failed_run_reruns_with_new_answers(settings, db, world):
    node, _, task = world
    _plan(db, node)
    request = db.query(HumanRequest).one()
    _answer_review(db, node)
    run = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, run, node, "failed", {"exit_code": 1}, "exit_nonzero", NOW)
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert task.status == "problem"

    with TestClient(create_app(settings)) as client:
        client.post("/login", data={"password": ADMIN_PASSWORD})
        page = client.get(f"/tasks/{task.id}").text
        assert "사람이 다시 입력" in page
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        client.post(f"/tasks/{task.id}/reopen-input", data={"csrf": csrf})
    db.refresh(task)
    db.refresh(request)
    assert task.status == "waiting_human" and request.status == "pending"
    assert request.answered_by == "human" and request.answers == {}
    assert request.answered_by_model is None
    human.submit(db, request, {"G003P1": {"human_label": "PARTIAL"}, "G003P2": {}}, NOW)
    scheduler.tick(db, NOW)
    rerun = leasing.lease_step(db, node, NOW)
    assert rerun.kind == "run" and rerun.attempt == run.attempt + 1
    assert '"PARTIAL"' in rerun.input["files"][".research-hub/answers.jsonl"]


FORM_B = {**FORM, "answers_path": ".research-hub/signoff.jsonl"}
PLAN_THEN_IMPLEMENT = {**PLAN, "needs_implement": True}
IMPLEMENT_ASKS = {"summary": "i", "run": {"command": "python lock.py", "timeout_hours": 1}, "human_input": FORM_B}


def _second_form_review_fails(db, node, task):
    """Plan form answered by the agent, implement asks a second form, its review keeps failing."""
    _plan(db, node, PLAN_THEN_IMPLEMENT)
    _answer_review(db, node)
    implement = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, implement, node, "succeeded", IMPLEMENT_ASKS, None, NOW)
    scheduler.tick(db, NOW)
    db.refresh(task)
    while task.status == "running":
        review = leasing.lease_step(db, node, NOW)
        assert review.kind == "review"
        leasing.complete_step(db, review, node, "failed", None, "bad_result", NOW)
        scheduler.tick(db, NOW)
        db.refresh(task)
    assert task.status == "problem"
    first, second = db.query(HumanRequest).order_by(HumanRequest.id).all()
    assert first.status == "submitted" and second.status == "pending"
    return first, second


def test_reopen_hands_the_pending_form_to_the_person_not_an_earlier_one(settings, db, world):
    node, _, task = world
    first, second = _second_form_review_fails(db, node, task)

    with TestClient(create_app(settings)) as client:
        client.post("/login", data={"password": ADMIN_PASSWORD})
        page = client.get(f"/tasks/{task.id}").text
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        client.post(f"/tasks/{task.id}/reopen-input", data={"csrf": csrf})
    for row in (task, first, second):
        db.refresh(row)
    assert task.status == "waiting_human"
    assert first.status == "submitted" and first.answers["G003P1"]["human_label"] == "ANSWERS"
    assert second.status == "pending" and second.answered_by == "human"

    human.submit(db, second, {"G003P1": {"human_label": "PARTIAL"}, "G003P2": {}}, NOW)
    scheduler.tick(db, NOW)
    run = leasing.lease_step(db, node, NOW)
    assert run.kind == "run" and run.input["command"] == "python lock.py"
    assert '"PARTIAL"' in run.input["files"][".research-hub/signoff.jsonl"]


def test_a_stale_pending_form_stops_the_task_loudly_instead_of_idling(db, world):
    node, project, task = world
    project.auto_approve = False
    db.commit()
    first, second = _second_form_review_fails(db, node, task)
    human.reopen(db, first, NOW)  # the earlier form reopened while the second one was still pending
    second.answered_by = "human"
    human.submit(db, second, {"G003P1": {"human_label": "PARTIAL"}, "G003P2": {}}, NOW)
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert task.status == "problem"
    assert db.query(Event).filter(Event.task_id == task.id, Event.level == "error",
                                  Event.message.contains(f"요청 {first.id}")).count() == 1


def test_an_agent_run_project_hands_a_stale_person_form_to_the_agent(db, world):
    node, _, task = world
    first, second = _second_form_review_fails(db, node, task)
    human.reopen(db, first, NOW)
    second.answered_by = "human"
    human.submit(db, second, {"G003P1": {"human_label": "PARTIAL"}, "G003P2": {}}, NOW)
    scheduler.tick(db, NOW)

    db.refresh(task)
    db.refresh(first)
    assert task.status == "running" and first.answered_by == "llm"
    assert db.query(Event).filter(Event.task_id == task.id, Event.message.contains("에이전트가 채운다")).count() == 1
    review = _answer_review(db, node)
    assert review.kind == "review"
    db.refresh(first)
    assert first.status == "submitted" and first.answered_by == "llm"
    assert leasing.lease_step(db, node, NOW) is not None
