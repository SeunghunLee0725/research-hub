import re

import pytest
from fastapi.testclient import TestClient

from hub.db.models import Project, Task, Upload
from hub.main import create_app
from hub.services.nodes import create_node
from tests.conftest import ADMIN_PASSWORD


@pytest.fixture()
def client(settings, db):
    create_node(db, "spark-2588", ["gpu", "claude", "codex"])
    with TestClient(create_app(settings)) as c:
        c.post("/login", data={"password": ADMIN_PASSWORD})
        yield c


def _csrf(page):
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def _create(client, **extra):
    page = client.get("/projects/new").text
    data = {"csrf": _csrf(page), "slug": "xray-new", "name": "X선 신규 과제", "node": "spark-2588",
            "workdir": "", "context": "방어 목적 연구", "plan": "# 계획\n1. 자료 조사", **extra}
    return client.post("/projects", data=data, follow_redirects=False)


def test_new_project_form_and_create(client, db):
    page = client.get("/projects/new").text
    assert "새 프로젝트" in page and "spark-2588" in page and 'name="plan"' in page
    resp = _create(client)
    assert resp.status_code == 303 and resp.headers["location"] == "/projects/xray-new"
    project = db.query(Project).one()
    assert project.workdir == "~/research_projects/xray-new"
    assert project.node_selector == ["node:spark-2588"]
    assert project.context == "방어 목적 연구"
    plan = db.query(Upload).one()
    assert plan.path == "plan.md" and plan.data == "# 계획\n1. 자료 조사".encode()


def test_create_validation(client, db):
    assert _create(client, slug="Bad Slug").status_code == 422
    assert _create(client, node="nope").status_code == 422
    assert _create(client, workdir="relative/path").status_code == 422
    _create(client)
    assert _create(client).status_code == 409


def test_project_page_upload_and_task(client, db):
    _create(client)
    page = client.get("/projects/xray-new").text
    assert "X선 신규 과제" in page and "dropzone" in page and "plan.md" in page
    csrf = _csrf(page)
    resp = client.post("/projects/xray-new/uploads", headers={"X-CSRF-Token": csrf},
                       files=[("files", ("paper.pdf", b"%PDF-1.4", "application/pdf")),
                              ("files", ("data.csv", b"a,b", "text/csv"))])
    assert resp.status_code == 200 and [u["path"] for u in resp.json()["data"]] == ["materials/paper.pdf", "materials/data.csv"]
    assert client.post("/projects/xray-new/uploads", headers={"X-CSRF-Token": "bad"},
                       files=[("files", ("x.txt", b"x", "text/plain"))]).status_code == 403
    assert "materials/paper.pdf" in client.get("/projects/xray-new").text

    resp = client.post("/projects/xray-new/tasks", follow_redirects=False, data={
        "csrf": csrf, "title": "자료 정리", "objective": "올린 논문 요약", "criteria": "요약 파일", "start": "1"})
    task = db.query(Task).one()
    assert resp.headers["location"] == f"/tasks/{task.id}" and task.status == "approved"


def test_project_settings_update(client, db):
    _create(client)
    csrf = _csrf(client.get("/projects/xray-new").text)
    client.post("/projects/xray-new/settings", data={"csrf": csrf, "context": "새 배경", "name": "이름 변경"})
    project = db.query(Project).one()
    db.refresh(project)
    assert (project.context, project.name, project.auto_ai_review) == ("새 배경", "이름 변경", False)


def test_overview_links_projects(client, db):
    _create(client)
    page = client.get("/").text
    assert 'href="/projects/new"' in page and 'href="/projects/xray-new"' in page
