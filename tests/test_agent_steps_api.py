import pytest
from fastapi.testclient import TestClient

from hub.db.models import Project, Step
from hub.main import create_app
from hub.services import scheduler, tasks
from hub.services.nodes import create_node
from tests.conftest import NOW

PLAN_OK = {"summary": "s", "approach": ["a"], "needs_implement": False, "run": None}


@pytest.fixture()
def setup(settings, db):
    node, token = create_node(db, "spark-dbb1", ["gpu", "claude"])
    _, other_token = create_node(db, "spark-2588", ["gpu", "claude"])
    project = Project(slug="p", name="P", workdir="/w", node_selector=["node:spark-dbb1"])
    db.add(project)
    db.commit()
    task = tasks.create_task(db, project, "t", "o", None)
    tasks.approve_start(db, task, NOW)
    scheduler.tick(db, NOW)
    with TestClient(create_app(settings)) as client:
        yield client, {"Authorization": f"Bearer {token}"}, {"Authorization": f"Bearer {other_token}"}


def test_lease_progress_complete(setup, db):
    client, auth, _ = setup
    step = client.post("/agent/v1/lease", headers=auth).json()["data"]["step"]
    assert step["kind"] == "plan" and step["workdir"] == "/w" and step["prompt"]
    assert client.post("/agent/v1/lease", headers=auth).json()["data"]["step"] is None
    assert client.post(f"/agent/v1/steps/{step['id']}/progress", json={"message": "working"},
                       headers=auth).status_code == 200
    resp = client.post(f"/agent/v1/steps/{step['id']}/complete", headers=auth,
                       json={"status": "succeeded", "result": PLAN_OK, "error_class": None})
    assert resp.status_code == 200
    assert db.get(Step, step["id"]).status == "succeeded"


def test_other_node_gets_nothing_and_cannot_complete(setup):
    client, auth, other = setup
    assert client.post("/agent/v1/lease", headers=other).json()["data"]["step"] is None
    step = client.post("/agent/v1/lease", headers=auth).json()["data"]["step"]
    resp = client.post(f"/agent/v1/steps/{step['id']}/complete", headers=other,
                       json={"status": "succeeded", "result": PLAN_OK, "error_class": None})
    assert resp.status_code == 409


def test_complete_validates_status(setup):
    client, auth, _ = setup
    step = client.post("/agent/v1/lease", headers=auth).json()["data"]["step"]
    resp = client.post(f"/agent/v1/steps/{step['id']}/complete", headers=auth,
                       json={"status": "weird", "result": None, "error_class": None})
    assert resp.status_code == 422


def test_unknown_step_is_404(setup):
    client, auth, _ = setup
    assert client.post("/agent/v1/steps/9999/progress", json={"message": "x"}, headers=auth).status_code == 404
