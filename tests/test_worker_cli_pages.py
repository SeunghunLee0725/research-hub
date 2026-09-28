import pytest
from fastapi.testclient import TestClient

from hub import cli, worker
from hub.db.models import Event, Project, Task
from hub.main import create_app
from hub.services import tasks
from hub.services.overview import build_overview
from tests.conftest import ADMIN_PASSWORD, NOW


@pytest.fixture()
def project(db):
    p = Project(slug="plasma-kg", name="플라즈마 KG", workdir="/w", node_selector=["node:spark-dbb1"])
    db.add(p)
    db.commit()
    return p


def test_cli_add_task_and_approve(db, settings, project, capsys):
    assert cli.main(["add-task", "plasma-kg", "검색 회귀 조사", "--objective", "recall 회복",
                     "--criteria", "recall>=0.9"], settings) == 0
    task = db.query(Task).one()
    assert task.status == "proposed" and task.success_criteria == "recall>=0.9"
    assert cli.main(["approve-start", str(task.id)], settings) == 0
    db.refresh(task)
    assert task.status == "approved"
    assert cli.main(["approve-start", str(task.id)], settings) == 1


def test_cli_add_task_rejects_second_active_task(db, settings, project, capsys):
    cli.main(["add-task", "plasma-kg", "a", "--objective", "o"], settings)
    assert cli.main(["add-task", "plasma-kg", "b", "--objective", "o"], settings) == 1
    assert "진행 중인 작업" in capsys.readouterr().err


def test_worker_cycle_runs_scheduler_and_notifier(db, settings, project):
    task = tasks.create_task(db, project, "t", "o", None)
    tasks.approve_start(db, task, NOW)
    sent = []
    worker.cycle(db, NOW, settings, sender=sent.append, notify_now=True)
    db.refresh(task)
    assert task.status == "running"
    worker.cycle(db, NOW, settings, sender=None, notify_now=True)


def test_overview_shows_latest_event(db, project):
    task = tasks.create_task(db, project, "t", "o", None)
    db.add(Event(task_id=task.id, message="epoch 3/10"))
    db.commit()
    (p,) = build_overview(db, NOW, 90).projects
    assert p.last_event == "epoch 3/10" and p.task_id == task.id


def test_task_page(settings, db, project):
    task = tasks.create_task(db, project, "검색 회귀 조사", "recall 회복", None)
    task.status, task.result_card = "review", {
        "conclusion": "recall 0.93 회복", "what_we_did": "w",
        "metrics": [{"name": "recall@5", "baseline": "0.90", "result": "0.93", "note": ""}],
        "trust": {"verified": True, "notes": ["원자료 재계산 일치"]}, "limits": ["교과서만 평가"],
        "next_options": [{"title": "논문 recall 분리", "why": "y", "est_hours": 2}]}
    db.commit()
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/tasks/{task.id}", follow_redirects=False).status_code == 303
        client.post("/login", data={"password": ADMIN_PASSWORD})
        page = client.get(f"/tasks/{task.id}")
        assert page.status_code == 200
        for text in ("검색 회귀 조사", "recall 0.93 회복", "원자료 재계산 일치", "교과서만 평가", "논문 recall 분리"):
            assert text in page.text
        assert client.get("/tasks/99999").status_code == 404
