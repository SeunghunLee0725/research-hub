import pytest
from fastapi.testclient import TestClient

from hub.db.models import Node, QuotaSnapshot
from hub.main import create_app
from hub.services.nodes import create_node

HEARTBEAT = {
    "agent_version": "0.1.0",
    "hostname": "spark-a",
    "metrics": {
        "load": [1.0, 0.5, 0.2],
        "mem_total_mb": 120000,
        "mem_used_mb": 30000,
        "disk_total_gb": 3700,
        "disk_used_gb": 400,
        "gpus": [{"name": "NVIDIA GB10", "util_pct": 61, "mem_used_mb": None, "mem_total_mb": None}],
        "gpu_procs": [{"pid": 42, "name": "python", "mem_mb": 2048}],
    },
}


@pytest.fixture()
def client(settings, db):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture()
def node_token(db):
    _, token = create_node(db, "spark-a", ["gpu", "claude"])
    return token


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_heartbeat_requires_token(client):
    assert client.post("/agent/v1/heartbeat", json=HEARTBEAT).status_code == 401
    assert client.post("/agent/v1/heartbeat", json=HEARTBEAT, headers=_auth("nope")).status_code == 401


def test_heartbeat_updates_node(client, db, node_token):
    resp = client.post("/agent/v1/heartbeat", json=HEARTBEAT, headers=_auth(node_token))
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "data": {"node": "spark-a"}, "error": None}
    node = db.query(Node).filter_by(name="spark-a").one()
    db.refresh(node)
    assert node.last_seen_at is not None
    assert node.metrics["gpus"][0]["util_pct"] == 61
    assert node.agent_version == "0.1.0"


def test_disabled_node_is_rejected(client, db, node_token):
    node = db.query(Node).filter_by(name="spark-a").one()
    node.disabled = True
    db.commit()
    assert client.post("/agent/v1/heartbeat", json=HEARTBEAT, headers=_auth(node_token)).status_code == 403


def test_heartbeat_validates_payload(client, node_token):
    bad = {**HEARTBEAT, "metrics": {**HEARTBEAT["metrics"], "gpus": [{"name": "x", "util_pct": 500}]}}
    assert client.post("/agent/v1/heartbeat", json=bad, headers=_auth(node_token)).status_code == 422


def test_logins_are_stored(client, db, node_token):
    payload = {
        "claude": {"logged_in": True, "auth_method": "claude.ai", "subscription": "max", "error": None},
        "codex": {"logged_in": False, "auth_method": None, "subscription": None, "error": "not logged in"},
    }
    assert client.post("/agent/v1/logins", json=payload, headers=_auth(node_token)).status_code == 200
    node = db.query(Node).filter_by(name="spark-a").one()
    db.refresh(node)
    assert node.logins["claude"]["logged_in"] is True
    assert node.logins["codex"]["error"] == "not logged in"
    assert node.logins["checked_at"]


def test_quota_snapshot_is_recorded(client, db, node_token):
    payload = {
        "provider": "claude",
        "windows": [{"id": "five_hour", "label": "5시간", "used_percent": 58.0,
                     "resets_at": "2026-09-28T05:00:00+00:00", "window_minutes": 300}],
        "error": None,
    }
    assert client.post("/agent/v1/quota", json=payload, headers=_auth(node_token)).status_code == 200
    snap = db.query(QuotaSnapshot).one()
    assert snap.provider == "claude" and snap.windows[0]["used_percent"] == 58.0


def test_quota_rejects_unknown_provider(client, node_token):
    payload = {"provider": "gemini", "windows": [], "error": None}
    assert client.post("/agent/v1/quota", json=payload, headers=_auth(node_token)).status_code == 422
