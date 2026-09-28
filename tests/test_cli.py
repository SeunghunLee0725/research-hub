from hub import cli
from hub.db.models import Node, Project
from hub.security import hash_token, verify_password


def test_add_node_prints_token_once_and_stores_hash(db, settings, capsys):
    assert cli.main(["add-node", "spark-a", "--labels", "gpu,claude,codex"], settings) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    node = db.query(Node).filter_by(name="spark-a").one()
    assert node.token_hash == hash_token(token)
    assert node.labels == ["gpu", "claude", "codex"]


def test_add_node_rejects_bad_name(db, settings, capsys):
    assert cli.main(["add-node", "bad name!"], settings) == 2
    assert db.query(Node).count() == 0


def test_add_node_duplicate_fails_cleanly(db, settings, capsys):
    cli.main(["add-node", "spark-a"], settings)
    assert cli.main(["add-node", "spark-a"], settings) == 1
    assert "이미" in capsys.readouterr().err


def test_rotate_token(db, settings, capsys):
    cli.main(["add-node", "spark-a"], settings)
    old = capsys.readouterr().out.strip().splitlines()[-1]
    assert cli.main(["rotate-token", "spark-a"], settings) == 0
    new = capsys.readouterr().out.strip().splitlines()[-1]
    db.expire_all()
    assert db.query(Node).one().token_hash == hash_token(new) != hash_token(old)


def test_add_project(db, settings):
    assert cli.main(["add-project", "plasma-kg", "플라즈마 KG", "/home/x/plasma-kg/repo",
                     "--node-labels", "gpu"], settings) == 0
    project = db.query(Project).one()
    assert (project.slug, project.name, project.node_selector) == ("plasma-kg", "플라즈마 KG", ["gpu"])


def test_hash_password(capsys, settings, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "pw-123456")
    assert cli.main(["hash-password"], settings) == 0
    assert verify_password("pw-123456", capsys.readouterr().out.strip())
