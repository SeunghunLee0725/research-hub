import pytest
from fastapi.testclient import TestClient

from hub.main import create_app
from hub.services.nodes import create_node
from hub.services.projects import create_project
from tests.conftest import ADMIN_PASSWORD


@pytest.fixture()
def client(settings, db):
    with TestClient(create_app(settings)) as c:
        yield c


def _login(client, password=ADMIN_PASSWORD):
    return client.post("/login", data={"password": password}, follow_redirects=False)


def test_overview_requires_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/login"
    assert client.get("/partials/overview").status_code == 401


def test_wrong_password_is_rejected(client):
    assert _login(client, "nope").status_code == 401


def test_login_is_rate_limited(client):
    for _ in range(5):
        _login(client, "nope")
    assert _login(client, ADMIN_PASSWORD).status_code == 429


def test_login_then_overview_shows_nodes_and_projects(client, db):
    create_node(db, "spark-a", ["gpu"])
    create_project(db, "plasma-kg", "플라즈마 KG", "/w", [])
    resp = _login(client)
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    page = client.get("/")
    assert page.status_code == 200
    assert "spark-a" in page.text and "플라즈마 KG" in page.text
    assert "spark-a" in client.get("/partials/overview").text


def test_logout_clears_session(client):
    _login(client)
    client.post("/logout")
    assert client.get("/partials/overview").status_code == 401


def test_health_is_public(client):
    assert client.get("/healthz").json() == {"success": True, "data": {"db": "ok"}, "error": None}
