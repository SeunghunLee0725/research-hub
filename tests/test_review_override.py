import re

import pytest
from fastapi.testclient import TestClient

from hub.db.models import HumanRequest, Node, Project, Task
from hub.main import create_app
from hub.services import leasing, scheduler, tasks
from tests.conftest import ADMIN_PASSWORD, NOW

FORM = {"instructions": "판정", "answers_path": "a.jsonl",
        "fields": [{"name": "label", "label": "판정", "type": "choice", "choices": ["Y", "N"]}],
        "items": [{"id": "i1", "title": "항목 1"}]}
PLAN = {"summary": "s", "approach": ["a"], "needs_implement": False,
        "run": {"command": "x", "timeout_hours": 1}, "human_input": FORM}
CARD = {"conclusion": "미검증", "what_we_did": "w", "metrics": [], "trust": {"verified": False, "notes": []},
        "limits": [], "next_options": [{"title": "사람이 직접 판정", "why": "LLM 판정 애매", "est_hours": 1}]}


@pytest.fixture()
def world(db):
    node = Node(name="spark-dbb1", token_hash="a" * 64, labels=["claude", "codex"])
    project = Project(slug="plasma-kg", name="플라즈마 KG", workdir="/w", node_selector=["node:spark-dbb1"],
                      auto_ai_review=True)
    db.add_all([node, project])
    db.commit()
    return node, project


def _to_form(db, node, task):
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, step, node, "succeeded", PLAN, None, NOW)
    scheduler.tick(db, NOW)
    db.refresh(task)


def test_human_override_beats_project_auto_mode(db, world):
    node, project = world
    task = tasks.create_task(db, project, "t", "o", None)
    tasks.approve_start(db, task, NOW, review_by="human")
    _to_form(db, node, task)
    assert task.status == "waiting_human"
    assert db.query(HumanRequest).one().answered_by == "human"


def test_default_follows_project(db, world):
    node, project = world
    task = tasks.create_task(db, project, "t", "o", None)
    tasks.approve_start(db, task, NOW)
    _to_form(db, node, task)
    assert task.status == "running" and db.query(HumanRequest).one().answered_by == "llm"


def test_result_approval_can_pick_human_review(db, world):
    _, project = world
    task = tasks.create_task(db, project, "t", "o", None)
    tasks.approve_start(db, task, NOW)
    task.status, task.result_card = "review", CARD
    tasks.open_result_approval(db, task)
    db.commit()
    follow = tasks.approve_result(db, task, 0, NOW, review_by="human")
    assert follow.review_by == "human"


@pytest.fixture()
def client(settings, db):
    with TestClient(create_app(settings)) as c:
        c.post("/login", data={"password": ADMIN_PASSWORD})
        yield c


def _csrf(page):
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def test_web_checkbox_on_result_and_start(client, db, world):
    _, project = world
    task = tasks.create_task(db, project, "t", "o", None)
    tasks.approve_start(db, task, NOW)
    task.status, task.result_card = "review", CARD
    tasks.open_result_approval(db, task)
    db.commit()
    page = client.get(f"/tasks/{task.id}").text
    assert 'name="human_review"' in page and "사람이 판정" in page
    client.post(f"/tasks/{task.id}/approve-result", data={"csrf": _csrf(page), "option": "0", "human_review": "1"})
    follow = db.query(Task).filter_by(parent_task_id=task.id).one()
    assert follow.review_by == "human"
    assert "사람이 판정" in client.get(f"/tasks/{follow.id}").text

    other = Project(slug="x", name="X", workdir="/w")
    db.add(other)
    db.commit()
    proposed = tasks.create_task(db, other, "p", "o", None)
    page = client.get(f"/tasks/{proposed.id}").text
    assert 'name="human_review"' in page
    client.post(f"/tasks/{proposed.id}/approve-start", data={"csrf": _csrf(page)})
    db.refresh(proposed)
    assert proposed.status == "approved" and proposed.review_by is None
