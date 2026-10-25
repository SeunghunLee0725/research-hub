"""끝난 프로젝트는 종료한다: 새 작업이 생기지 않고, 기록과 작업 폴더는 그대로 남는다."""
import re

import pytest
from fastapi.testclient import TestClient

from hub.db.models import Event, Node, Project, Step, Task, Upload
from hub.main import create_app
from hub.services import projects, scheduler, tasks, uploads
from hub.services.overview import build_overview
from tests.conftest import ADMIN_PASSWORD, NOW


@pytest.fixture()
def world(db):
    node = Node(name="spark-2588", token_hash="a" * 64, labels=["claude", "codex"])
    project = Project(slug="plasma-af3", name="산화 잔기 매핑", workdir="/w", node_selector=["node:spark-2588"])
    db.add_all([node, project])
    db.commit()
    return node, project


def test_close_cancels_the_open_task_and_keeps_its_record(db, world):
    _, project = world
    task = tasks.create_task(db, project, "후속 작업", "에이전트가 제안한 다음 단계", None)

    projects.close_project(db, project, NOW, "논문 제출 완료")

    db.refresh(task)
    assert project.closed_at == NOW and project.closed_note == "논문 제출 완료"
    assert task.status == "cancelled"
    assert db.query(Task).filter(Task.project_id == project.id).count() == 1
    assert any("프로젝트 종료" in e.message for e in db.query(Event).filter(Event.task_id == task.id))


def test_a_closed_project_takes_no_new_work(db, world):
    _, project = world
    projects.close_project(db, project, NOW, None)

    with pytest.raises(ValueError, match="종료"):
        tasks.create_task(db, project, "새 작업", "목표", None)
    with pytest.raises(ValueError, match="종료"):
        uploads.add_upload(db, project, "plan.md", b"# plan")
    with pytest.raises(ValueError):
        projects.close_project(db, project, NOW, None)


def test_the_scheduler_starts_nothing_for_a_closed_project(db, world):
    _, project = world
    task = tasks.create_task(db, project, "후속 작업", "목표", None)
    projects.close_project(db, project, NOW, None)

    scheduler.tick(db, NOW)

    db.refresh(task)
    assert task.status == "cancelled"
    assert db.query(Step).filter(Step.task_id == task.id).count() == 0


def test_reopen_lets_work_resume(db, world):
    _, project = world
    projects.close_project(db, project, NOW, "끝")

    projects.reopen_project(db, project)

    assert project.closed_at is None and project.closed_note is None
    tasks.create_task(db, project, "다시 시작", "목표", None)
    with pytest.raises(ValueError):
        projects.reopen_project(db, project)


def test_overview_marks_closed_projects(db, world):
    _, project = world
    projects.close_project(db, project, NOW, None)

    view = build_overview(db, NOW, 120)

    assert [p.status for p in view.projects] == ["closed"]
    assert view.projects[0].closed_at == NOW


def test_close_and_reopen_from_the_project_page(settings, db, world):
    _, project = world
    uploads.add_upload(db, project, "materials/data.csv", b"a,b\n1,2\n")
    with TestClient(create_app(settings)) as client:
        client.post("/login", data={"password": ADMIN_PASSWORD})
        page = client.get(f"/projects/{project.slug}").text
        assert "프로젝트 종료" in page
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)

        assert client.post(f"/projects/{project.slug}/close", data={"csrf": csrf},
                           follow_redirects=False).status_code == 422  # 확인 체크 없이는 종료하지 않는다
        resp = client.post(f"/projects/{project.slug}/close", follow_redirects=False,
                           data={"csrf": csrf, "confirm": "1", "note": "최종 보고서 제출"})
        assert resp.status_code == 303

        page = client.get(f"/projects/{project.slug}").text
        assert "종료됨" in page and "최종 보고서 제출" in page and "/w" in page
        assert 'action="/projects/plasma-af3/tasks"' not in page
        assert client.post(f"/projects/{project.slug}/tasks", follow_redirects=False,
                           data={"csrf": csrf, "title": "t", "objective": "o"}).status_code == 409
        assert "종료된 프로젝트" in client.get("/").text

        assert client.post(f"/projects/{project.slug}/reopen", data={"csrf": csrf},
                           follow_redirects=False).status_code == 303
    db.refresh(project)
    assert project.closed_at is None
    assert db.query(Upload).filter(Upload.project_id == project.id).count() == 1
