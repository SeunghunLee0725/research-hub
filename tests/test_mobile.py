import pytest
from fastapi.testclient import TestClient

from hub.db.models import Event, Node, Project, Step
from hub.main import create_app
from hub.services import tasks
from hub.services.overview import build_overview
from tests.conftest import ADMIN_PASSWORD, NOW

S25 = ("Mozilla/5.0 (Linux; Android 15; SM-S931N) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/129.0 Mobile Safari/537.36")
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Safari/605.1.15"


@pytest.fixture()
def data(db):
    node = Node(name="spark-dbb1", token_hash="a" * 64, labels=["claude"], last_seen_at=NOW,
                logins={"claude": {"logged_in": True}, "codex": {"logged_in": True}})
    p1 = Project(slug="plasma-kg", name="플라즈마 KG", workdir="/w")
    p2 = Project(slug="drug", name="마약탐지", workdir="/w")
    p3 = Project(slug="idle", name="쉬는 과제", workdir="/w")
    db.add_all([node, p1, p2, p3])
    db.commit()
    running = tasks.create_task(db, p1, "검색 품질 측정", "o", None)
    tasks.approve_start(db, running, NOW)
    running.status, running.node_id = "running", node.id
    db.add(Step(task_id=running.id, seq=3, kind="run", model="none", status="running", started_at=NOW))
    db.add(Event(task_id=running.id, message="epoch 3/10"))
    waiting = tasks.create_task(db, p2, "X선 80장 판독", "o", None)
    db.commit()
    return running, waiting


def test_overview_exposes_current_step(db, data):
    running, _ = data
    view = build_overview(db, NOW, 90)
    p = next(p for p in view.projects if p.slug == "plasma-kg")
    assert (p.step_kind, p.step_status) == ("run", "running")


@pytest.fixture()
def client(settings, db):
    with TestClient(create_app(settings)) as c:
        c.post("/login", data={"password": ADMIN_PASSWORD})
        yield c


def test_phone_gets_mobile_layout(client, data):
    page = client.get("/", headers={"User-Agent": S25}).text
    assert 'class="mobile"' in page and 'name="viewport"' in page
    assert page.index("확인 필요") < page.index("진행 중") < page.index("머신")
    assert "X선 80장 판독" in page and "검색 품질 측정" in page and "실행" in page and "epoch 3/10" in page
    assert "쉬는 과제" in page and 'href="/?view=desktop"' in page


def test_desktop_gets_desktop_layout(client, data):
    page = client.get("/", headers={"User-Agent": MAC}).text
    assert 'class="mobile"' not in page and 'href="/?view=mobile"' in page


def test_view_preference_is_remembered(client, data):
    client.get("/?view=desktop", headers={"User-Agent": S25})
    assert 'class="mobile"' not in client.get("/", headers={"User-Agent": S25}).text
    client.get("/?view=mobile", headers={"User-Agent": MAC})
    assert 'class="mobile"' in client.get("/", headers={"User-Agent": MAC}).text


def test_mobile_partial_refresh(client, data):
    client.get("/", headers={"User-Agent": S25})
    part = client.get("/partials/overview", headers={"User-Agent": S25}).text
    assert "m-card" in part


def test_task_page_marks_mobile(client, data):
    _, waiting = data
    page = client.get(f"/tasks/{waiting.id}", headers={"User-Agent": S25}).text
    assert 'class="mobile"' in page
