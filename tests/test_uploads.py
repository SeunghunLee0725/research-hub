import pytest
from fastapi.testclient import TestClient

from hub.db.models import Project, Upload
from hub.main import create_app
from hub.services import uploads
from hub.services.nodes import create_node
from hub.services.prompts import build_prompt
from hub.db.models import Task


def test_target_path_for_uploads():
    assert uploads.target_path("plan.md") == "plan.md"
    assert uploads.target_path("PLAN.MD") == "plan.md"
    assert uploads.target_path("논문 A (2024).pdf") == "materials/논문 A (2024).pdf"
    assert uploads.target_path("../../etc/passwd") == "materials/passwd"
    assert uploads.target_path("C:\\dir\\data.csv") == "materials/data.csv"
    for bad in ("", ".", "..", ".hidden"):
        with pytest.raises(ValueError):
            uploads.target_path(bad)


def test_prompt_tells_model_to_read_plan_first():
    project = Project(slug="p", name="P", workdir="/w")
    prompt = build_prompt("plan", project, Task(title="t", objective="o"), {}, "/r")
    assert "plan.md" in prompt and "materials/" in prompt


@pytest.fixture()
def setup(settings, db):
    node, token = create_node(db, "spark-2588", ["claude"])
    _, other = create_node(db, "spark-dbb1", ["claude"])
    project = Project(slug="new", name="새 과제", workdir="~/research_projects/new",
                      node_selector=["node:spark-2588"])
    db.add(project)
    db.commit()
    with TestClient(create_app(settings)) as client:
        yield client, {"Authorization": f"Bearer {token}"}, {"Authorization": f"Bearer {other}"}, project


def test_agent_sync_download_and_confirm(setup, db):
    client, auth, other, project = setup
    up = uploads.add_upload(db, project, "data.csv", b"a,b\n1,2\n")
    data = client.post("/agent/v1/sync", headers=auth, json={"projects": {}}).json()["data"]
    assert data["projects"] == [{"id": project.id, "slug": "new", "workdir": "~/research_projects/new"}]
    assert data["uploads"] == [{"id": up.id, "project_id": project.id, "path": "materials/data.csv", "size": 8}]
    assert client.post("/agent/v1/sync", headers=other, json={"projects": {}}).json()["data"]["uploads"] == []
    assert client.get(f"/agent/v1/uploads/{up.id}/content", headers=other).status_code == 404
    content = client.get(f"/agent/v1/uploads/{up.id}/content", headers=auth)
    assert content.status_code == 200 and content.content == b"a,b\n1,2\n"
    assert client.post(f"/agent/v1/uploads/{up.id}/result", headers=auth,
                       json={"ok": True, "written_path": "/home/x/research_projects/new/materials/data.csv"}).status_code == 200
    db.refresh(up)
    assert up.status == "delivered" and up.data is None
    client.post("/agent/v1/sync", headers=auth, json={"projects": {str(project.id): {"ok": True, "error": None}}})
    db.refresh(project)
    assert project.workdir_status == {"ok": True, "error": None}


def test_upload_size_limit(db):
    project = Project(slug="p", name="P", workdir="/w")
    db.add(project)
    db.commit()
    with pytest.raises(ValueError):
        uploads.add_upload(db, project, "big.bin", b"0" * (uploads.MAX_BYTES + 1))
