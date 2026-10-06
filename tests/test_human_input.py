import json
import re

import pytest
from fastapi.testclient import TestClient

from hub.db.models import HumanRequest, Node, Project, Step, Task
from hub.main import create_app
from hub.services import human, leasing, notify, scheduler, tasks
from hub.services.pipeline import StepOutcome, retry_decision, validate_result
from tests.conftest import ADMIN_PASSWORD, NOW

FORM = {
    "instructions": "각 논문이 질문에 직접 답하는지 판정하세요.",
    "answers_path": ".research-hub/answers/human_labels.jsonl",
    "fields": [{"name": "human_label", "label": "판정", "type": "choice",
                "choices": ["ANSWERS", "PARTIAL", "OFF_TOPIC"]},
               {"name": "human_reason", "label": "이유", "type": "text"}],
    "items": [{"id": "G003-28325093", "title": "G003 · PMID:28325093", "body": "질문: PAM 기전?\n초록: ...",
               "priority": 1},
              {"id": "G020-1", "title": "G020 · PMID:1", "body": "초록", "priority": 2}],
    "requires_human": True,
}
PLAN = {"summary": "s", "approach": ["a"], "needs_implement": False,
        "run": {"command": "python score.py", "timeout_hours": 1}, "human_input": FORM}


def test_human_input_is_validated():
    assert validate_result("plan", PLAN)["human_input"]["items"][0]["id"] == "G003-28325093"
    for bad_path in ("/etc/passwd", "../x.jsonl", "a/../../x"):
        with pytest.raises(ValueError):
            validate_result("plan", {**PLAN, "human_input": {**FORM, "answers_path": bad_path}})
    with pytest.raises(ValueError):
        validate_result("plan", {**PLAN, "human_input": {**FORM, "fields": [
            {"name": "x", "label": "x", "type": "choice", "choices": []}]}})


def test_run_step_failures_are_not_retried_except_lost():
    assert retry_decision("exit_nonzero", 1, "run") == StepOutcome.PROBLEM
    assert retry_decision("timeout", 1, "run") == StepOutcome.PROBLEM
    assert retry_decision("lost", 1, "run") == StepOutcome.RETRY
    assert retry_decision("timeout", 1, "plan") == StepOutcome.RETRY
    assert retry_decision("exit_nonzero", 1, "implement") == StepOutcome.PROBLEM


def test_answers_jsonl():
    answers = {"G003-28325093": {"human_label": "ANSWERS", "human_reason": "기전 제시"}, "G020-1": {}}
    lines = human.answers_jsonl(FORM, answers).splitlines()
    assert lines == ['{"id": "G003-28325093", "human_label": "ANSWERS", "human_reason": "기전 제시"}']


def test_parse_form_answers_rejects_invalid_choice():
    form_data = {"G003-28325093__human_label": "ANSWERS", "G003-28325093__human_reason": " 이유 ",
                 "G020-1__human_label": "", "evil__human_label": "ANSWERS"}
    assert human.parse_answers(FORM, form_data) == {
        "G003-28325093": {"human_label": "ANSWERS", "human_reason": "이유"}, "G020-1": {}}
    with pytest.raises(ValueError):
        human.parse_answers(FORM, {"G003-28325093__human_label": "MAYBE"})


@pytest.fixture()
def world(db):
    node = Node(name="spark-dbb1", token_hash="a" * 64, labels=["claude", "codex"])
    project = Project(slug="p", name="플라즈마", workdir="/w", node_selector=["node:spark-dbb1"],
                      auto_ai_review=False)  # this project's forms are answered by a person
    db.add_all([node, project])
    db.commit()
    task = tasks.create_task(db, project, "사람 판정", "o", None)
    tasks.approve_start(db, task, NOW)
    return node, task


def _plan_with_form(db, node):
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, step, node, "succeeded", PLAN, None, NOW)
    scheduler.tick(db, NOW)


def test_step_requesting_input_pauses_task_until_submitted(db, world):
    node, task = world
    _plan_with_form(db, node)
    db.refresh(task)
    assert task.status == "waiting_human"
    request = db.query(HumanRequest).one()
    assert request.status == "pending" and request.form["items"][0]["id"] == "G003-28325093"
    scheduler.tick(db, NOW)
    assert leasing.lease_step(db, node, NOW) is None

    human.save(db, request, {"G003-28325093": {"human_label": "PARTIAL"}}, NOW)
    db.refresh(task)
    assert task.status == "waiting_human"
    human.submit(db, request, {"G003-28325093": {"human_label": "ANSWERS"}}, NOW)
    db.refresh(task)
    assert task.status == "running"
    scheduler.tick(db, NOW)
    run = leasing.lease_step(db, node, NOW)
    assert run.kind == "run"
    files = run.input["files"]
    assert files[".research-hub/answers/human_labels.jsonl"] == '{"id": "G003-28325093", "human_label": "ANSWERS"}\n'
    assert json.loads(files[".research-hub/answers/human_labels.jsonl.provenance.json"])["submitted_by"] == "human"


def test_waiting_human_blocks_new_task_and_alerts(db, world):
    node, task = world
    _plan_with_form(db, node)
    project = db.get(Project, task.project_id)
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        tasks.create_task(db, project, "another", "o", None)
    db.rollback()
    sent = []
    notify.deliver(db, NOW, sent.append, "http://hub", 90)
    assert any("사람 작업 필요" in s and "/tasks/" in s for s in sent)


@pytest.fixture()
def client(settings, db):
    with TestClient(create_app(settings)) as c:
        c.post("/login", data={"password": ADMIN_PASSWORD})
        yield c


def test_web_form_save_and_submit(client, db, world):
    node, task = world
    _plan_with_form(db, node)
    page = client.get(f"/tasks/{task.id}").text
    assert "사람 작업 필요" in page and "G003 · PMID:28325093" in page and "OFF_TOPIC" in page
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    resp = client.post(f"/tasks/{task.id}/human-input", follow_redirects=False,
                       data={"csrf": csrf, "action": "save", "G003-28325093__human_label": "PARTIAL"})
    assert resp.status_code == 303
    assert 'value="PARTIAL" checked' in client.get(f"/tasks/{task.id}").text
    resp = client.post(f"/tasks/{task.id}/human-input", follow_redirects=False,
                       data={"csrf": csrf, "action": "submit", "G003-28325093__human_label": "ANSWERS"})
    assert resp.status_code == 303
    db.refresh(task)
    assert task.status == "running"
    assert client.post(f"/tasks/{task.id}/human-input", data={"csrf": csrf, "action": "submit"}).status_code == 409


def test_web_form_rejects_bad_choice(client, db, world):
    node, task = world
    _plan_with_form(db, node)
    csrf = re.search(r'name="csrf" value="([^"]+)"', client.get(f"/tasks/{task.id}").text).group(1)
    resp = client.post(f"/tasks/{task.id}/human-input",
                       data={"csrf": csrf, "action": "submit", "G003-28325093__human_label": "MAYBE"})
    assert resp.status_code == 422


def test_web_form_handles_items_without_priority(client, db, world):
    node, task = world
    form = {**FORM, "items": [{"id": "a", "title": "A 항목", "body": "b", "priority": None},
                              {"id": "b", "title": "B 항목", "body": "b", "priority": 1}]}
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, step, node, "succeeded", {**PLAN, "human_input": form}, None, NOW)
    scheduler.tick(db, NOW)
    page = client.get(f"/tasks/{task.id}").text
    assert page.index("B 항목") < page.index("A 항목")


def test_a_person_submitting_a_delegated_form_is_recorded_as_human(db, world):
    """The form may have been handed to the agent, but this submission came from the researcher."""
    node, task = world
    _plan_with_form(db, node)
    request = db.query(HumanRequest).one()
    request.answered_by, request.answered_by_model = "llm", "claude/claude-opus-5-5"
    db.commit()

    human.submit(db, request, {"G003-28325093": {"human_label": "ANSWERS"},
                               "G020-1": {"human_label": "OFF_TOPIC"}}, NOW)

    db.refresh(request)
    assert request.answered_by == "human"
    assert request.answered_by_model is None
    record = json.loads(human.submitted_files(db, task)[FORM["answers_path"] + ".provenance.json"])
    assert record["submitted_by"] == "human"
