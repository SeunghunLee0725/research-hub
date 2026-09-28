import base64
import re

import pytest
from fastapi.testclient import TestClient

from hub.db.models import Media, Project
from hub.main import create_app
from hub.services import leasing, scheduler, tasks
from hub.services.nodes import create_node
from hub.services.pipeline import validate_result
from tests.conftest import ADMIN_PASSWORD, NOW

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
FORM = {"instructions": "물체유형을 고르세요.", "answers_path": "x3a/answers.jsonl", "layout": "table",
        "fields": [{"name": "object_type", "label": "물체유형", "type": "choice",
                    "choices": ["suitcase", "backpack", "unsure"]},
                   {"name": "note", "label": "메모", "type": "text"}],
        "items": [{"id": "det-001", "title": "DET 001", "image": "x3a/thumbs/det-001.png"},
                  {"id": "det-002", "title": "DET 002", "image": "x3a/thumbs/det-002.png"}]}
PLAN = {"summary": "s", "approach": ["a"], "needs_implement": False,
        "run": {"command": "python score.py", "timeout_hours": 1}, "human_input": FORM}


def test_item_image_path_must_be_relative():
    assert validate_result("plan", PLAN)["human_input"]["layout"] == "table"
    bad = {**FORM, "items": [{"id": "a", "title": "a", "image": "../../etc/passwd"}]}
    with pytest.raises(ValueError):
        validate_result("plan", {**PLAN, "human_input": bad})


@pytest.fixture()
def setup(settings, db):
    node, token = create_node(db, "spark-2588", ["claude", "codex"])
    _, other = create_node(db, "spark-dbb1", ["claude"])
    project = Project(slug="drug", name="마약탐지", workdir="/w", node_selector=["node:spark-2588"])
    db.add(project)
    db.commit()
    task = tasks.create_task(db, project, "X3a 사람 검수", "o", None)
    tasks.approve_start(db, task, NOW)
    scheduler.tick(db, NOW)
    step = leasing.lease_step(db, node, NOW)
    with TestClient(create_app(settings)) as client:
        yield client, {"Authorization": f"Bearer {token}"}, {"Authorization": f"Bearer {other}"}, node, step, task


def _upload(client, auth, step_id, path, data=PNG, content_type="image/png"):
    return client.post(f"/agent/v1/steps/{step_id}/media", headers=auth, json={
        "path": path, "content_type": content_type, "data_b64": base64.b64encode(data).decode()})


def test_agent_uploads_media_for_its_step(setup, db):
    client, auth, other, _, step, _ = setup
    assert _upload(client, auth, step.id, "x3a/thumbs/det-001.png").status_code == 200
    assert _upload(client, auth, step.id, "x3a/thumbs/det-001.png").status_code == 200  # idempotent
    assert db.query(Media).count() == 1
    assert _upload(client, other, step.id, "x3a/thumbs/det-002.png").status_code == 409
    assert _upload(client, auth, step.id, "x.svg", content_type="image/svg+xml").status_code == 422
    assert _upload(client, auth, step.id, "big.png", data=b"0" * (5 * 1024 * 1024 + 1)).status_code == 413


def test_table_form_shows_images_to_admin_only(setup, db):
    client, auth, _, node, step, task = setup
    _upload(client, auth, step.id, "x3a/thumbs/det-001.png")
    leasing.complete_step(db, step, node, "succeeded", PLAN, None, NOW)
    scheduler.tick(db, NOW)
    media = db.query(Media).one()
    assert client.get(f"/media/{media.id}").status_code == 401
    client.post("/login", data={"password": ADMIN_PASSWORD})
    resp = client.get(f"/media/{media.id}")
    assert resp.status_code == 200 and resp.content == PNG and resp.headers["content-type"] == "image/png"
    assert "nosniff" in resp.headers["x-content-type-options"]
    page = client.get(f"/tasks/{task.id}").text
    assert '<table class="review">' in page
    assert f'src="/media/{media.id}"' in page
    assert "이미지 없음" in page  # det-002 was never uploaded
    assert page.count('name="det-001__object_type"') == 3
