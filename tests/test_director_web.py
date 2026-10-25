"""디렉터 화면: 헌장 확정, 질문 응답, 착수 버튼, 텔레그램 알림."""
import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from hub.db.models import DirectorDecision, Node, Project, Task
from hub.main import create_app
from hub.services import director, notify, tasks
from hub.web.director import milestones_text, parse_milestones
from tests.conftest import ADMIN_PASSWORD, NOW
from tests.test_director import CHARTER, CARD


@pytest.fixture()
def world(db):
    node = Node(name="spark-dbb1", token_hash="a" * 64, labels=["claude", "codex"])
    project = Project(slug="drug", name="마약탐지", workdir="/w", node_selector=["node:spark-dbb1"],
                      director_enabled=True)
    db.add_all([node, project])
    db.commit()
    return project


@pytest.fixture()
def client(settings, db):
    with TestClient(create_app(settings)) as c:
        c.post("/login", data={"password": ADMIN_PASSWORD})
        yield c


def _csrf(page):
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def _at_gate(db, project, action, payload, reason="근거"):
    task = tasks.create_task(db, project, "판독 기준 재검토", "o", None)
    task.status, task.result_card = "review", CARD
    tasks.open_result_approval(db, task)
    row = DirectorDecision(project_id=project.id, task_id=task.id, action=action, status="pending",
                           reason=reason, payload=payload, model="claude/claude-opus-5-5")
    db.add(row)
    db.commit()
    return task, row


def test_milestone_lines_round_trip():
    text = milestones_text(CHARTER)
    assert parse_milestones(text) == CHARTER["milestones"]
    assert parse_milestones("M3 | 새 단계 | 조건")[0]["status"] == "open"
    with pytest.raises(ValueError):
        parse_milestones("M3 제목만")


def test_idle_project_offers_kickoff(client, db, world):
    page = client.get("/projects/drug").text
    assert "디렉터 착수" in page
    resp = client.post("/projects/drug/kickoff", data={"csrf": _csrf(page)}, follow_redirects=False)
    assert resp.status_code == 303
    assert db.query(Task).one().title == director.KICKOFF_TITLE


def test_charter_draft_is_confirmed_from_the_project_page(client, db, world):
    task, _ = _at_gate(db, world, "charter", {}, "기준선은 끝났다")
    world.charter = CHARTER
    db.commit()
    page = client.get("/projects/drug").text
    assert "초안 — 확인 필요" in page and "M1 | 기준선 | 기준선 recall 측정 | 완료" in page
    assert "연구 헌장 확인" in client.get(f"/tasks/{task.id}").text

    data = {"csrf": _csrf(page), "question": "새 질문", "milestones": milestones_text(CHARTER) + "\nM3 | 보고 | 보고서",
            "deliverables": "보고서\n그림", "boundaries": "", "max_tasks": "8", "max_run_hours": "", "deadline": ""}
    assert client.post("/projects/drug/charter", data=data, follow_redirects=False).status_code == 303
    db.refresh(world)
    assert world.charter_confirmed_at is not None and world.charter["question"] == "새 질문"
    assert len(world.charter["milestones"]) == 3 and world.charter["budget"]["max_tasks"] == 8
    assert director.pending_decision(db, task) is None

    bad = {**data, "milestones": "M1 | 제목 | 조건 | 몰라"}
    assert client.post("/projects/drug/charter", data=bad).status_code == 422


def test_answering_a_conflict_from_the_task_page(client, db, world):
    director.confirm_charter(db, world, CHARTER, NOW)
    task, row = _at_gate(db, world, "ask", {"question": "기준선과 충돌", "options": ["재측정", "폐기"]})
    page = client.get(f"/tasks/{task.id}").text
    assert "핵심 결론 충돌" in page and "재측정" in page
    resp = client.post(f"/director/{row.id}/answer", follow_redirects=False,
                       data={"csrf": _csrf(page), "choice": "재측정", "comment": "시드 고정"})
    assert resp.status_code == 303
    db.refresh(row)
    assert row.status == "answered" and row.answer["text"] == "재측정 — 시드 고정"
    assert client.post(f"/director/{row.id}/answer", data={"csrf": _csrf(page), "choice": "x"}).status_code == 409


def test_turning_the_director_off(client, db, world):
    page = client.get("/projects/drug").text
    client.post("/projects/drug/director", data={"csrf": _csrf(page), "enabled": "0"})
    db.refresh(world)
    assert world.director_enabled is False
    assert "디렉터에게 맡기기" in client.get("/projects/drug").text


def test_new_projects_can_be_handed_to_the_director(client, db, world):
    page = client.get("/projects/new").text
    client.post("/projects", data={"csrf": _csrf(page), "slug": "new-one", "name": "새", "node": "spark-dbb1",
                                   "director": "1"})
    assert db.query(Project).filter(Project.slug == "new-one").one().director_enabled is True


def test_director_questions_reach_telegram_once(db, world):
    director.confirm_charter(db, world, CHARTER, NOW)
    _at_gate(db, world, "budget", {"question": "예산을 넘겼다: 작업 5/5개"})
    sent = []
    notify.deliver(db, NOW, sent.append, "http://hub", 120)
    notify.deliver(db, NOW, sent.append, "http://hub", 120)
    budget = [s for s in sent if "예산 초과" in s]
    assert len(budget) == 1 and "작업 5/5개" in budget[0]


def test_weekly_digest_is_sent_on_monday_morning(db, world):
    director.confirm_charter(db, world, CHARTER, NOW)
    monday = datetime(2026, 10, 26, 0, 30, tzinfo=timezone.utc)  # 09:30 KST
    sunday = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    sent = []
    notify.deliver(db, sunday, sent.append, "http://hub", 10 ** 9)
    assert not any("주간 연구 요약" in s for s in sent)
    notify.deliver(db, monday, sent.append, "http://hub", 10 ** 9)
    notify.deliver(db, monday, sent.append, "http://hub", 10 ** 9)
    digests = [s for s in sent if "주간 연구 요약" in s]
    assert len(digests) == 1 and "마약탐지" in digests[0] and "마일스톤 1/2" in digests[0]
