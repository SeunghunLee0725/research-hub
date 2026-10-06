import json

from agent.steps.result_file import prepare, read_result


def test_tilde_in_the_result_path_resolves_to_the_home_directory(tmp_path, monkeypatch):
    """The hub stores a project workdir as the user typed it, so '~' can reach the agent."""
    monkeypatch.setenv("HOME", str(tmp_path))
    result_path = prepare("~/work/.research-hub/steps/7/result.json")

    assert result_path == tmp_path / "work/.research-hub/steps/7/result.json"
    assert result_path.parent.is_dir()
    result_path.write_text(json.dumps({"summary": "ok"}))
    assert read_result(result_path) == {"summary": "ok"}


def test_prepare_removes_a_previous_attempts_result(tmp_path):
    path = tmp_path / "steps/7/result.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"summary": "stale"}))

    assert read_result(prepare(str(path))) is None
