from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from hub.db.models import Node, Project, Step, Task
from hub.main import create_app
from tests.conftest import ADMIN_PASSWORD
from tests.test_mobile import S25

H = timedelta(hours=1)


@pytest.fixture()
def client(settings, db):
    with TestClient(create_app(settings), headers={"User-Agent": S25}) as c:
        c.post("/login", data={"password": ADMIN_PASSWORD})
        yield c


@pytest.fixture()
def data(db):
    now = datetime.now(timezone.utc)
    node = Node(name="shlee-a4000", token_hash="a" * 64, last_seen_at=now)
    grant = Project(slug="drug", name="마약탐지 과제", workdir="/w", kind="research", ends_on=date(2026, 12, 31),
                    node_selector=["node:spark-2588"])
    short = Project(slug="ion", name="이온소스 단기", workdir="/w", kind="short")
    db.add_all([node, grant, short])
    db.commit()
    running = Task(project_id=short.id, title="음극 PIC 스윕", objective="o", status="running", node_id=node.id,
                   created_at=now - 3 * H)
    problem = Task(project_id=grant.id, title="라벨 검수 실패", objective="o", status="problem", created_at=now - 5 * H)
    db.add_all([running, problem])
    db.commit()
    db.add_all([
        Step(task_id=running.id, seq=1, kind="plan", model="claude", status="succeeded", node_id=node.id,
             started_at=now - 2 * H, ended_at=now - 1.5 * H),
        Step(task_id=running.id, seq=2, kind="run", model="none", status="running", node_id=node.id,
             started_at=now - 1.5 * H),
    ])
    db.commit()
    return running, problem


def test_mobile_pages_require_login(settings, db):
    with TestClient(create_app(settings)) as anon:
        for path in ("/m", "/m/board", "/m/timeline", "/m/projects"):
            resp = anon.get(path, follow_redirects=False)
            assert resp.status_code == 303 and resp.headers["location"] == "/login"
        assert anon.get("/m?partial=1").status_code == 401


def test_mobile_board_has_status_chips(client, data):
    running, problem = data
    page = client.get("/m/board").text
    assert 'data-chip="running"' in page and 'data-chip="problem"' in page
    assert f'href="/tasks/{running.id}"' in page and "라벨 검수 실패" in page
    assert 'data-status="running"' in page
    assert 'class="tab on" href="/m/board"' in page


def test_mobile_timeline_strips(client, data):
    running, _ = data
    page = client.get("/m/timeline?range=1").text
    assert "m-strip" in page and "음극 PIC 스윕" in page
    assert 'class="sg agent-none live"' in page and 'class="sg agent-claude"' in page
    assert "라벨 검수 실패" in page  # problem task without steps still listed
    assert client.get("/m/timeline?range=30&kind=short").status_code == 200


def test_mobile_projects_split_by_kind(client, data):
    page = client.get("/m/projects").text
    assert 'data-pane="research"' in page and 'data-pane="short"' in page
    assert page.index('data-pane="research"') < page.index("마약탐지 과제") < page.index('data-pane="short"')
    assert "D-" in page or "마감" in page
    assert "spark-2588" in page  # the project's machine, even with no task running


def test_manifest_is_public(settings, db):
    with TestClient(create_app(settings)) as anon:
        resp = anon.get("/manifest.webmanifest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["start_url"] == "/m" and body["display"] == "standalone" and body["icons"]


def test_badges(client, data):
    resp = client.get("/partials/badges")
    assert resp.json()["data"] == {"attention": 1, "running": 1, "problem": 1}
