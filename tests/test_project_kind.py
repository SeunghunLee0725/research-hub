import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from hub.db.models import Project
from hub.main import create_app
from hub.services.nodes import create_node
from tests.conftest import ADMIN_PASSWORD


@pytest.fixture()
def client(settings, db):
    create_node(db, "shlee-a4000", ["gpu"])
    with TestClient(create_app(settings)) as c:
        c.post("/login", data={"password": ADMIN_PASSWORD})
        yield c


def _csrf(page):
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def _create(client, **extra):
    page = client.get("/projects/new").text
    data = {"csrf": _csrf(page), "slug": "ion", "name": "이온소스", "node": "shlee-a4000", **extra}
    return client.post("/projects", data=data, follow_redirects=False)


def test_new_project_form_offers_kind_and_period(client):
    page = client.get("/projects/new").text
    assert 'name="kind"' in page and 'name="starts_on"' in page and 'name="ends_on"' in page
    assert "shlee-a4000" in page


def test_create_short_project_with_deadline(client, db):
    assert _create(client, kind="short", ends_on="2026-11-30").status_code == 303
    p = db.query(Project).one()
    assert p.kind == "short" and p.starts_on is None and p.ends_on == date(2026, 11, 30)


def test_default_kind_is_research(client, db):
    _create(client)
    assert db.query(Project).one().kind == "research"


def test_kind_and_period_are_validated(client, db):
    assert _create(client, kind="forever").status_code == 422
    assert _create(client, starts_on="2026-12-01", ends_on="2026-11-01").status_code == 422
    assert _create(client, ends_on="not-a-date").status_code == 422


def test_settings_change_kind_and_period(client, db):
    _create(client)
    page = client.get("/projects/ion").text
    assert "연구과제" in page
    resp = client.post("/projects/ion/settings", data={"csrf": _csrf(page), "name": "이온소스", "kind": "short",
                                                       "starts_on": "2026-10-01", "ends_on": "2026-10-31"},
                       follow_redirects=False)
    assert resp.status_code == 303
    db.expire_all()
    p = db.query(Project).one()
    assert (p.kind, p.starts_on, p.ends_on) == ("short", date(2026, 10, 1), date(2026, 10, 31))


def test_cli_add_project_with_kind(settings, db):
    from hub import cli
    assert cli.main(["add-project", "grant", "과제", "/w", "--kind", "short"], settings=settings) == 0
    assert db.query(Project).one().kind == "short"
