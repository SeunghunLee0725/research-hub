from datetime import timedelta

from hub.db.models import Node, Project, QuotaSnapshot, Task
from hub.services.overview import build_overview, login_badge, node_state
from tests.conftest import NOW


def test_node_state():
    assert node_state(None, NOW, 90) == "never"
    assert node_state(NOW - timedelta(seconds=30), NOW, 90) == "online"
    assert node_state(NOW - timedelta(seconds=91), NOW, 90) == "offline"


def test_login_badge():
    assert login_badge(None) == "unknown"
    assert login_badge({"logged_in": True}) == "ok"
    assert login_badge({"logged_in": False}) == "logged_out"
    assert login_badge({"logged_in": None, "error": "timeout"}) == "unknown"


def test_overview_collects_nodes_quota_and_projects(db):
    online = Node(name="spark-a", token_hash="a" * 64, labels=["gpu"], last_seen_at=NOW,
                  metrics={"gpus": [{"name": "GB10", "util_pct": 70}]},
                  logins={"claude": {"logged_in": True}, "codex": {"logged_in": False}})
    stale = Node(name="spark-b", token_hash="b" * 64, last_seen_at=NOW - timedelta(minutes=5))
    project = Project(slug="plasma-kg", name="플라즈마 KG", workdir="/w")
    db.add_all([online, stale, project])
    db.commit()
    db.add_all([
        QuotaSnapshot(node_id=online.id, provider="claude", at=NOW - timedelta(minutes=20),
                      windows=[{"id": "five_hour", "label": "5시간", "used_percent": 10.0}]),
        QuotaSnapshot(node_id=stale.id, provider="claude", at=NOW - timedelta(minutes=5),
                      windows=[{"id": "five_hour", "label": "5시간", "used_percent": 42.0}]),
        Task(project_id=project.id, title="검색 회귀 조사", objective="o", status="running",
             node_id=online.id),
    ])
    db.commit()

    view = build_overview(db, NOW, offline_after_seconds=90)

    states = {n.name: n.state for n in view.nodes}
    assert states == {"spark-a": "online", "spark-b": "offline"}
    a = next(n for n in view.nodes if n.name == "spark-a")
    assert a.gpu_util == [70] and a.claude == "ok" and a.codex == "logged_out"
    assert [q.provider for q in view.quotas] == ["claude"]
    assert view.quotas[0].windows[0]["used_percent"] == 42.0
    (p,) = view.projects
    assert p.name == "플라즈마 KG" and p.task_title == "검색 회귀 조사"
    assert p.status == "running" and p.node == "spark-a"


def test_project_without_active_task_is_idle(db):
    project = Project(slug="x", name="X", workdir="/w")
    db.add(project)
    db.commit()
    db.add(Task(project_id=project.id, title="old", objective="o", status="done"))
    db.commit()
    (p,) = build_overview(db, NOW, offline_after_seconds=90).projects
    assert p.status == "idle" and p.task_title is None
