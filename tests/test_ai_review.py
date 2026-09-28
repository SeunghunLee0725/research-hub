import json
import re

import pytest
from fastapi.testclient import TestClient

from hub.db.models import HumanRequest, Node, Project, Step
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


@pytest.fixture()
def world(db):
    node = Node(name="spark-dbb1", token_hash="a" * 64, labels=["claude", "codex"])
    project = Project(slug="plasma-kg", name="플라즈마 KG", workdir="/w", node_selector=["node:spark-dbb1"],
                      auto_ai_review=True)
    db.add_all([node, project])
    db.commit()
    task = tasks.create_task(db, project, "논문 판정", "o", None)
    tasks.approve_start(db, task, NOW)
    return node, project, task


def _complete_next(db, node, result, expect_kind):
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, node, NOW)
    assert step.kind == expect_kind
    leasing.complete_step(db, step, node, "succeeded", result, None, NOW)
    return step


def test_auto_ai_review_fills_form_and_continues(db, world):
    node, _, task = world
    _complete_next(db, node, PLAN, "plan")
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert task.status == "running"
    request = db.query(HumanRequest).one()
    assert request.status == "pending" and request.answered_by == "llm"
    review = leasing.lease_step(db, node, NOW)
    assert review.kind == "review" and review.model == "claude"
    form_file = ".research-hub/steps/%d/form.json" % review.id
    assert json.loads(review.input["files"][form_file])["items"][1]["id"] == "G003P2"
    assert form_file in review.input["prompt"] and "사람 대신" in review.input["prompt"]
    leasing.complete_step(db, review, node, "succeeded", REVIEW_OK, None, NOW)
    scheduler.tick(db, NOW)
    db.refresh(request)
    assert request.status == "submitted" and request.answers["G003P1"]["human_label"] == "ANSWERS"
    run = leasing.lease_step(db, node, NOW)
    assert run.kind == "run" and run.seq == review.seq + 1
    assert '"G003P2"' in run.input["files"][".research-hub/answers.jsonl"]


def test_invalid_ai_answers_are_retried(db, world):
    node, _, task = world
    _complete_next(db, node, PLAN, "plan")
    scheduler.tick(db, NOW)
    review = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, review, node, "succeeded",
                          {"summary": "s", "answers": {"G003P1": {"human_label": "MAYBE"}}}, None, NOW)
    scheduler.tick(db, NOW)
    retry = leasing.lease_step(db, node, NOW)
    assert retry.kind == "review" and retry.attempt == 2
    assert db.get(Step, review.id).error_class == "bad_result"


def test_manual_delegate_button(settings, db, world):
    node, project, task = world
    project.auto_ai_review = False
    db.commit()
    _complete_next(db, node, PLAN, "plan")
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert task.status == "waiting_human"
    with TestClient(create_app(settings)) as client:
        client.post("/login", data={"password": ADMIN_PASSWORD})
        page = client.get(f"/tasks/{task.id}").text
        assert "AI에게 맡기기" in page
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        resp = client.post(f"/tasks/{task.id}/delegate-ai", data={"csrf": csrf}, follow_redirects=False)
        assert resp.status_code == 303
    db.refresh(task)
    assert task.status == "running"
    assert db.query(HumanRequest).one().answered_by == "llm"
    scheduler.tick(db, NOW)
    assert leasing.lease_step(db, node, NOW).kind == "review"


def test_validate_ai_answers():
    assert human.validate_answers(FORM, REVIEW_OK["answers"])["G003P2"] == {"human_label": "OFF_TOPIC"}
    with pytest.raises(ValueError):
        human.validate_answers(FORM, {"UNKNOWN": {"human_label": "ANSWERS"}})
    with pytest.raises(ValueError):
        human.validate_answers(FORM, {"G003P1": {"bogus_field": "x"}})
