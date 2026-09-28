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
                      auto_ai_review=True)  # legacy flag: must no longer bypass the person
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


def test_default_is_human_even_with_legacy_project_flag(db, world):
    node, _, task = world
    _plan(db, node)
    db.refresh(task)
    assert task.status == "waiting_human"
    assert db.query(HumanRequest).one().answered_by == "human"


def test_delegation_is_one_shot(db, world):
    node, _, task = world
    _plan(db, node)
    request = db.query(HumanRequest).one()
    human.delegate_to_ai(db, request, NOW)
    scheduler.tick(db, NOW)
    review = leasing.lease_step(db, node, NOW)
    assert review.kind == "review"
    form_file = ".research-hub/steps/%d/form.json" % review.id
    assert json.loads(review.input["files"][form_file])["items"][1]["id"] == "G003P2"
    leasing.complete_step(db, review, node, "succeeded", REVIEW_OK, None, NOW)
    scheduler.tick(db, NOW)
    db.refresh(request)
    assert request.status == "submitted" and request.answered_by == "llm"
    run = leasing.lease_step(db, node, NOW)
    assert run.kind == "run" and '"G003P2"' in run.input["files"][".research-hub/answers.jsonl"]
    # a later form in the same task goes back to the person
    leasing.complete_step(db, run, node, "succeeded", {"exit_code": 0, "duration_s": 1}, None, NOW)
    scheduler.tick(db, NOW)
    analyze = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, analyze, node, "succeeded",
                          {"summary": "s", "findings": [], "human_input": {**FORM, "answers_path": "b.jsonl"}}, None, NOW)
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert task.status == "waiting_human"


def test_invalid_ai_answers_are_retried(db, world):
    node, _, task = world
    _plan(db, node)
    human.delegate_to_ai(db, db.query(HumanRequest).one(), NOW)
    scheduler.tick(db, NOW)
    review = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, review, node, "succeeded",
                          {"summary": "s", "answers": {"G003P1": {"human_label": "MAYBE"}}}, None, NOW)
    scheduler.tick(db, NOW)
    retry = leasing.lease_step(db, node, NOW)
    assert retry.kind == "review" and retry.attempt == 2
    assert db.get(Step, review.id).error_class == "bad_result"


def test_requires_human_blocks_delegation(settings, db, world):
    node, _, task = world
    _plan(db, node, {**PLAN, "human_input": {**FORM, "requires_human": True}})
    request = db.query(HumanRequest).one()
    with pytest.raises(ValueError):
        human.delegate_to_ai(db, request, NOW)
    with TestClient(create_app(settings)) as client:
        client.post("/login", data={"password": ADMIN_PASSWORD})
        page = client.get(f"/tasks/{task.id}").text
        assert "AI에게 맡기기" not in page and "연구자 본인" in page


def test_delegate_button(settings, db, world):
    node, _, task = world
    _plan(db, node)
    with TestClient(create_app(settings)) as client:
        client.post("/login", data={"password": ADMIN_PASSWORD})
        page = client.get(f"/tasks/{task.id}").text
        assert "AI에게 맡기기" in page and 'name="human_review"' not in page
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        assert client.post(f"/tasks/{task.id}/delegate-ai", data={"csrf": csrf},
                           follow_redirects=False).status_code == 303
    db.refresh(task)
    assert task.status == "running"


def test_reopen_after_failed_run_reruns_with_new_answers(settings, db, world):
    node, _, task = world
    _plan(db, node)
    request = db.query(HumanRequest).one()
    human.delegate_to_ai(db, request, NOW)
    scheduler.tick(db, NOW)
    review = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, review, node, "succeeded", REVIEW_OK, None, NOW)
    scheduler.tick(db, NOW)
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
    human.submit(db, request, {"G003P1": {"human_label": "PARTIAL"}, "G003P2": {}}, NOW)
    scheduler.tick(db, NOW)
    rerun = leasing.lease_step(db, node, NOW)
    assert rerun.kind == "run" and rerun.attempt == run.attempt + 1
    assert '"PARTIAL"' in rerun.input["files"][".research-hub/answers.jsonl"]
