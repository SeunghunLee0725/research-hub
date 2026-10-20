import re
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
    with TestClient(create_app(settings)) as c:
        c.post("/login", data={"password": ADMIN_PASSWORD})
        yield c


@pytest.fixture()
def data(db):
    now = datetime.now(timezone.utc)
    node = Node(name="shlee-a4000", token_hash="a" * 64, last_seen_at=now)
    grant = Project(slug="drug", name="마약탐지 과제", workdir="/w", kind="research", node_selector=["node:shlee-a4000"],
                    starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
    short = Project(slug="ion", name="이온소스 단기", workdir="/w", kind="short")
    db.add_all([node, grant, short])
    db.commit()
    task = Task(project_id=short.id, title="음극 PIC 스윕", objective="o", status="running", node_id=node.id,
                created_at=now - 3 * H)
    done = Task(project_id=grant.id, title="X3a 라벨 검수", objective="o", status="done", updated_at=now - H,
                created_at=now - 5 * H, result_card={"conclusion": "기준 통과"})
    db.add_all([task, done])
    db.commit()
    db.add_all([
        Step(task_id=task.id, seq=1, kind="plan", model="claude", status="succeeded", node_id=node.id,
             started_at=now - 2 * H, ended_at=now - 1.5 * H),
        Step(task_id=task.id, seq=2, kind="run", model="none", status="running", node_id=node.id,
             started_at=now - 1.5 * H),
        Step(task_id=done.id, seq=1, kind="implement", model="codex", status="succeeded", node_id=node.id,
             started_at=now - 4 * H, ended_at=now - 2 * H),
    ])
    db.commit()
    return task, done


def test_work_pages_require_login(settings, db):
    with TestClient(create_app(settings)) as anon:
        for path in ("/gantt", "/board"):
            resp = anon.get(path, follow_redirects=False)
            assert resp.status_code == 303 and resp.headers["location"] == "/login"


def test_gantt_by_project(client, data):
    task, done = data
    page = client.get("/gantt").text
    assert "연구과제" in page and "단기 프로젝트" in page
    assert "마약탐지 과제" in page and "이온소스 단기" in page
    assert f'href="/tasks/{task.id}"' in page and f'href="/tasks/{done.id}"' in page
    assert 'class="seg agent-codex' in page and 'class="seg agent-none' in page
    assert 'class="now"' in page


def test_gantt_filters_and_ranges(client, data):
    page = client.get("/gantt?kind=short&range=1").text
    assert "이온소스 단기" in page and "마약탐지 과제" not in page
    assert client.get("/gantt?range=all").status_code == 200
    assert client.get("/gantt?kind=bogus").status_code == 200  # unknown filter falls back to all


def test_gantt_by_agent(client, data):
    page = client.get("/gantt?view=agent").text
    assert "shlee-a4000" in page and "Claude" in page and "Codex" in page and "머신 실행" in page
    assert "이온소스 단기" in page  # legend


def test_gantt_and_board_on_phone_go_to_mobile_site(client, data):
    resp = client.get("/gantt", headers={"user-agent": S25}, follow_redirects=False)
    assert resp.headers["location"] == "/m/timeline"
    resp = client.get("/board", headers={"user-agent": S25}, follow_redirects=False)
    assert resp.headers["location"] == "/m/board"


def test_board_columns(client, data):
    task, _ = data
    page = client.get("/board").text
    for label in ("승인 대기", "실행 중", "결과 검토", "문제", "완료"):
        assert label in page
    assert "음극 PIC 스윕" in page and "기준 통과" in page
    assert re.search(r'data-status="running".*?음극 PIC 스윕', page, re.S)
    assert "이온소스 단기" in client.get("/board?kind=short").text
    assert "X3a 라벨 검수" not in client.get("/board?kind=short").text


def test_menu_links_on_every_page(client, data):
    page = client.get("/").text
    assert 'href="/gantt"' in page and 'href="/board"' in page


def test_overview_splits_projects_by_kind(client, data):
    page = client.get("/?view=desktop").text
    research = page.index("연구과제")
    short = page.index("단기 프로젝트")
    assert research < page.index("마약탐지 과제") < short < page.index("이온소스 단기")


def test_desktop_overview_has_summary_strip(client, data):
    page = client.get("/?view=desktop").text
    assert 'class="kpis"' in page and "실행 중" in page and "머신 온라인" in page


def test_filters_navigate_in_place_and_tooltips(client, data):
    page = client.get("/gantt").text
    assert "data-live-nav" in page and "data-tip=" in page and "/static/ui.js" in page
    assert 'data-badge="attention"' in page
