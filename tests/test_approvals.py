import re

import pytest
from fastapi.testclient import TestClient

from hub.db.models import Approval, Project, Step, Task
from hub.main import create_app
from hub.services import tasks
from tests.conftest import ADMIN_PASSWORD, NOW

CARD = {"conclusion": "c", "what_we_did": "w", "metrics": [], "trust": {"verified": False, "notes": []},
        "limits": [], "next_options": [{"title": "논문 recall 분리", "why": "교과서 문항 보호", "est_hours": 2}]}


@pytest.fixture()
def project(db):
    p = Project(slug="plasma-kg", name="플라즈마 KG", workdir="/w")
    db.add(p)
    db.commit()
    return p


def _review_task(db, project):
    task = tasks.create_task(db, project, "측정", "o", "c")
    tasks.approve_start(db, task, NOW)
    task.status, task.result_card = "review", CARD
    tasks.open_result_approval(db, task)
    db.commit()
    return task


def test_approve_result_with_next_option_starts_follow_up(db, project):
    task = _review_task(db, project)
    follow = tasks.approve_result(db, task, 0, NOW)
    db.refresh(task)
    assert task.status == "done"
    assert follow.status == "approved" and follow.title == "논문 recall 분리"
    assert follow.parent_task_id == task.id and "교과서 문항 보호" in follow.objective
    assert tasks.pending_approval(db, follow) is None


def test_approve_result_and_stop(db, project):
    task = _review_task(db, project)
    assert tasks.approve_result(db, task, None, NOW) is None
    db.refresh(task)
    assert task.status == "done"


def test_approve_result_rejects_bad_option(db, project):
    task = _review_task(db, project)
    with pytest.raises(ValueError):
        tasks.approve_result(db, task, 5, NOW)


def test_cancel_closes_pending_approval(db, project):
    task = tasks.create_task(db, project, "t", "o", None)
    tasks.cancel(db, task, NOW)
    assert task.status == "cancelled"
    assert db.query(Approval).filter_by(task_id=task.id).one().status == "rejected"


def test_retry_problem_creates_new_attempt(db, project):
    task = tasks.create_task(db, project, "t", "o", None)
    tasks.approve_start(db, task, NOW)
    task.status = "problem"
    db.add(Step(task_id=task.id, seq=1, kind="plan", model="claude", status="failed", attempt=3,
                error_class="timeout", input={"prompt": "p"}))
    db.commit()
    tasks.retry_problem(db, task, NOW)
    assert task.status == "running"
    new = db.query(Step).filter_by(task_id=task.id, attempt=4).one()
    assert new.status == "pending" and new.input == {"prompt": "p"}


@pytest.fixture()
def client(settings, db):
    with TestClient(create_app(settings)) as c:
        c.post("/login", data={"password": ADMIN_PASSWORD})
        yield c


def _csrf(client, task_id):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(f"/tasks/{task_id}").text).group(1)


def test_web_start_approval(client, db, project):
    task = tasks.create_task(db, project, "측정", "o", None)
    page = client.get(f"/tasks/{task.id}").text
    assert "시작 승인" in page
    resp = client.post(f"/tasks/{task.id}/approve-start", data={"csrf": _csrf(client, task.id)},
                       follow_redirects=False)
    assert resp.status_code == 303
    db.refresh(task)
    assert task.status == "approved"


def test_web_actions_require_csrf(client, db, project):
    task = tasks.create_task(db, project, "측정", "o", None)
    assert client.post(f"/tasks/{task.id}/approve-start", data={"csrf": "wrong"}).status_code == 403
    db.refresh(task)
    assert task.status == "proposed"


def test_web_result_approval_with_option(client, db, project):
    task = _review_task(db, project)
    assert "논문 recall 분리" in client.get(f"/tasks/{task.id}").text
    resp = client.post(f"/tasks/{task.id}/approve-result", data={"csrf": _csrf(client, task.id), "option": "0"},
                       follow_redirects=False)
    assert resp.status_code == 303
    follow = db.query(Task).filter_by(parent_task_id=task.id).one()
    assert resp.headers["location"] == f"/tasks/{follow.id}"


def test_web_wrong_state_is_conflict(client, db, project):
    task = tasks.create_task(db, project, "측정", "o", None)
    resp = client.post(f"/tasks/{task.id}/approve-result", data={"csrf": _csrf(client, task.id), "option": "stop"})
    assert resp.status_code == 409


def test_overview_lists_waiting_approvals(client, db, project):
    task = tasks.create_task(db, project, "측정", "o", None)
    page = client.get("/").text
    assert "확인 필요" in page and f"/tasks/{task.id}" in page
