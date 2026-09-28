from hub import cli
from hub.db.models import Project, Task
from hub.services.prompts import build_prompt


def test_prompt_includes_project_context():
    project = Project(slug="d", name="마약탐지", workdir="/w", context="방어 목적의 세관 탐지기 연구다.\n개발 범위는 알고리즘.")
    task = Task(title="X3a", objective="라벨", status="running")
    prompt = build_prompt("plan", project, task, {}, "/w/r.json")
    assert "## 프로젝트 배경" in prompt and "방어 목적의 세관 탐지기 연구다." in prompt


def test_prompt_without_context_has_no_section():
    project = Project(slug="d", name="P", workdir="/w", context=None)
    assert "## 프로젝트 배경" not in build_prompt("plan", project, Task(title="t", objective="o"), {}, "/r")


def test_cli_set_context_from_file(db, settings, tmp_path):
    cli.main(["add-project", "drug", "마약탐지", "/w"], settings)
    ctx = tmp_path / "ctx.md"
    ctx.write_text("방어 목적")
    assert cli.main(["set-context", "drug", str(ctx)], settings) == 0
    db.expire_all()
    assert db.query(Project).one().context == "방어 목적"
    assert cli.main(["set-context", "nope", str(ctx)], settings) == 1
