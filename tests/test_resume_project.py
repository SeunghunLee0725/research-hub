"""멈춘 프로젝트를 직전 작업 내용으로 이어서 다시 시작한다."""
import re

import pytest
from fastapi.testclient import TestClient

from hub.db.models import Node, Project, Task
from hub.main import create_app
from hub.services import leasing, scheduler, tasks
from tests.conftest import ADMIN_PASSWORD, NOW

PLAN = {"summary": "s", "approach": ["a"], "needs_implement": False,
        "run": {"command": "bash stage1.sh", "timeout_hours": 1,
                "expected_artifacts": ["out.csv"], "success_marker": "STAGE1_OK"}}


@pytest.fixture()
def world(db):
    node = Node(name="spark-2588", token_hash="a" * 64, labels=["claude", "codex"])
    project = Project(slug="plasma-af3", name="산화 잔기 매핑", workdir="/w",
                      node_selector=["node:spark-2588"], auto_approve=False)
    db.add_all([node, project])
    db.commit()
    task = tasks.create_task(db, project, "1단계: 환경 구축 + 예측", "네 종을 예측한다", "csv 4종 생성")
    tasks.approve_start(db, task, NOW)
    return node, project, task


def _fail_the_run(db, node, task):
    scheduler.tick(db, NOW)
    plan = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, plan, node, "succeeded", PLAN, None, NOW)
    scheduler.tick(db, NOW)
    run = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, run, node, "failed", {"exit_code": 1, "log_tail": "boom"},
                          "exit_nonzero", NOW)
    scheduler.tick(db, NOW)
    db.refresh(task)
    assert task.status == "problem"
    return run


def test_resume_carries_the_previous_objective_and_criteria(db, world):
    node, project, first = world
    _fail_the_run(db, node, first)
    tasks.cancel(db, first, NOW)

    resumed = tasks.resume_from(db, project, NOW)

    assert resumed.parent_task_id == first.id
    assert resumed.title == first.title
    assert first.objective in resumed.objective
    assert resumed.success_criteria == first.success_criteria
    assert resumed.status == "proposed"


def test_resume_explains_why_the_previous_attempt_stopped(db, world):
    node, project, first = world
    _fail_the_run(db, node, first)
    tasks.cancel(db, first, NOW)

    resumed = tasks.resume_from(db, project, NOW)

    assert f"작업 #{first.id}" in resumed.objective
    assert "run" in resumed.objective and "명령 실패" in resumed.objective
    assert "bash stage1.sh" in resumed.objective


def test_resume_needs_a_finished_task_and_an_idle_project(db, world):
    node, project, first = world
    with pytest.raises(ValueError):
        tasks.resume_from(db, project, NOW)  # the first task is still active

    tasks.cancel(db, first, NOW)
    tasks.resume_from(db, project, NOW)
    with pytest.raises(ValueError):
        tasks.resume_from(db, project, NOW)  # the resumed task is active now


def test_the_project_page_offers_the_resume_button(settings, db, world):
    node, project, first = world
    _fail_the_run(db, node, first)
    tasks.cancel(db, first, NOW)

    with TestClient(create_app(settings)) as client:
        client.post("/login", data={"password": ADMIN_PASSWORD})
        page = client.get(f"/projects/{project.slug}").text
        assert "직전 작업 이어서 시작" in page
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        resp = client.post(f"/projects/{project.slug}/resume", data={"csrf": csrf},
                           follow_redirects=False)
        assert resp.status_code == 303

    resumed = db.query(Task).filter(Task.parent_task_id == first.id).one()
    assert resumed.title == first.title
