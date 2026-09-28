import json
import stat
import sys
import textwrap
import time

import pytest

from agent.steps import claude_runner, run_job
from agent.executor import Executor


def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_classify_claude_output():
    ok = json.dumps({"type": "result", "is_error": False, "result": "done", "total_cost_usd": 0.5})
    assert claude_runner.classify(0, ok, "") is None
    limit = json.dumps({"is_error": True, "result": "You've hit your session limit · resets 3pm"})
    assert claude_runner.classify(1, limit, "") == "session_limit"
    assert claude_runner.classify(1, "", "Invalid API key · Please run /login") == "auth"
    refusal = json.dumps({"is_error": True, "result": "Sonnet 5 can't help with this [general_harms]"})
    assert claude_runner.classify(1, refusal, "") == "refusal"
    assert claude_runner.classify(1, "garbage", "boom") == "tool_error"


def test_build_command_never_skips_permissions():
    cmd = claude_runner.build_command("/bin/claude", "opus")
    assert "--dangerously-skip-permissions" not in cmd
    assert cmd[:2] == ["/bin/claude", "-p"] and "--output-format" in cmd and "opus" in cmd


def _step(tmp_path, **extra):
    result_path = tmp_path / ".research-hub/steps/1/result.json"
    return {"id": 1, "kind": "plan", "model": "claude", "workdir": str(tmp_path), "prompt": "do it",
            "result_path": str(result_path), "timeout_minutes": 1, **extra}


def test_run_model_step_reads_result_file(tmp_path):
    claude = _script(tmp_path, "claude", """
        import json, os, sys
        prompt = sys.stdin.read()
        path = os.path.join(os.getcwd(), ".research-hub/steps/1/result.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump({"summary": "planned", "echo": prompt}, open(path, "w"))
        print(json.dumps({"is_error": False, "result": "ok", "total_cost_usd": 0.1}))
        """)
    status, result, error = claude_runner.run_model_step(_step(tmp_path), claude, "opus")
    assert (status, error) == ("succeeded", None)
    assert result["summary"] == "planned" and result["echo"] == "do it"
    assert result["_meta"]["cost_usd"] == 0.1


def test_run_model_step_without_result_file(tmp_path):
    claude = _script(tmp_path, "claude", 'import json; print(json.dumps({"is_error": False, "result": "ok"}))')
    assert claude_runner.run_model_step(_step(tmp_path), claude, "opus") == ("failed", None, "no_result")


def test_run_model_step_timeout(tmp_path):
    claude = _script(tmp_path, "claude", "import time; time.sleep(5)")
    step = _step(tmp_path, timeout_minutes=0.02)
    assert claude_runner.run_model_step(step, claude, "opus")[2] == "timeout"


def _wait(job, state_dir, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        status = run_job.poll(job)
        if status.state != "running":
            return status
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_run_job_success_and_failure(tmp_path):
    state = tmp_path / "state"
    ok = run_job.launch({"id": 7, "workdir": str(tmp_path), "command": "echo hello; echo world",
                         "timeout_hours": 1}, state, use_systemd=False)
    status = _wait(ok, state)
    assert status.state == "done" and status.exit_code == 0 and "world" in status.log_tail
    bad = run_job.launch({"id": 8, "workdir": str(tmp_path), "command": "exit 3", "timeout_hours": 1},
                         state, use_systemd=False)
    assert _wait(bad, state).exit_code == 3
    assert {j.step_id for j in run_job.load_jobs(state)} == {7, 8}


def test_run_job_timeout_kills(tmp_path):
    state = tmp_path / "state"
    job = run_job.launch({"id": 9, "workdir": str(tmp_path), "command": "sleep 30",
                          "timeout_hours": 0.0001}, state, use_systemd=False)
    time.sleep(0.6)
    assert run_job.poll(job).state == "timeout"
    run_job.stop(job)
    run_job.forget(job, state)
    assert run_job.load_jobs(state) == []


class FakeClient:
    def __init__(self, steps):
        self.steps, self.calls = list(steps), []

    def post(self, path, payload):
        self.calls.append((path, payload))
        if path == "/agent/v1/lease":
            return {"step": self.steps.pop(0) if self.steps else None}
        return {}


def test_executor_runs_job_step_to_completion(tmp_path):
    step = {"id": 5, "kind": "run", "model": "none", "workdir": str(tmp_path),
            "command": "echo trained", "timeout_hours": 1}
    client = FakeClient([step])
    ex = Executor(client, tmp_path / "state", claude_bin="claude", model="opus", use_systemd=False)
    ex.tick(now=0)
    deadline = time.time() + 10
    while not any(p.endswith("/complete") for p, _ in client.calls) and time.time() < deadline:
        time.sleep(0.1)
        ex.tick(now=time.time())
    (path, payload), = [c for c in client.calls if c[0].endswith("/complete")]
    assert path == "/agent/v1/steps/5/complete"
    assert payload["status"] == "succeeded" and payload["result"]["exit_code"] == 0
    assert "trained" in payload["result"]["log_tail"]


def test_executor_runs_model_step_in_background(tmp_path):
    claude = _script(tmp_path, "claude", """
        import json, os
        path = os.path.join(os.getcwd(), ".research-hub/steps/1/result.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump({"summary": "s"}, open(path, "w"))
        print(json.dumps({"is_error": False, "result": "ok"}))
        """)
    client = FakeClient([_step(tmp_path)])
    ex = Executor(client, tmp_path / "state", claude_bin=claude, model="opus", use_systemd=False)
    ex.tick(now=0)
    ex.wait_model(timeout=10)
    complete = [p for p in client.calls if p[0].endswith("/complete")]
    assert complete[0][1]["status"] == "succeeded" and complete[0][1]["result"]["summary"] == "s"


def test_executor_dispatches_codex_steps(tmp_path):
    codex = _script(tmp_path, "codex", """
        import json, os, sys
        cwd = sys.argv[sys.argv.index("-C") + 1]
        path = os.path.join(cwd, ".research-hub/steps/1/result.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump({"verified": True, "checks": []}, open(path, "w"))
        """)
    client = FakeClient([_step(tmp_path, model="codex", kind="verify")])
    ex = Executor(client, tmp_path / "state", claude_bin="/nope", model="opus", use_systemd=False,
                  codex_bin=codex, codex_model=None)
    ex.tick(now=0)
    ex.wait_model(timeout=10)
    complete = [p for p in client.calls if p[0].endswith("/complete")]
    assert complete[0][1]["status"] == "succeeded" and complete[0][1]["result"]["verified"] is True


def test_complete_is_retried_through_hub_outage(tmp_path):
    from agent.client import HubError

    class Flaky:
        def __init__(self):
            self.calls = 0

        def post(self, path, payload):
            self.calls += 1
            if self.calls < 3:
                raise HubError("down")
            return {}

    client = Flaky()
    ex = Executor(client, tmp_path, claude_bin="c", model="m", use_systemd=False)
    ex._complete(1, "succeeded", {}, None, delays=(0, 0, 0))
    assert client.calls == 3


def test_write_step_files_stays_inside_workdir(tmp_path):
    from agent.steps.files import write_step_files
    write_step_files(str(tmp_path), {".research-hub/answers/a.jsonl": "x\n"})
    assert (tmp_path / ".research-hub/answers/a.jsonl").read_text() == "x\n"
    for bad in ("/tmp/evil", "../evil", "a/../../evil"):
        with pytest.raises(ValueError):
            write_step_files(str(tmp_path), {bad: "x"})


def test_executor_writes_files_before_run(tmp_path):
    step = {"id": 6, "kind": "run", "model": "none", "workdir": str(tmp_path),
            "command": "cat answers.jsonl", "timeout_hours": 1, "files": {"answers.jsonl": "LABELS"}}
    client = FakeClient([step])
    ex = Executor(client, tmp_path / "state", claude_bin="c", model="m", use_systemd=False)
    ex.tick(now=0)
    deadline = time.time() + 10
    while not any(p.endswith("/complete") for p, _ in client.calls) and time.time() < deadline:
        time.sleep(0.1)
        ex.tick(now=time.time())
    (_, payload), = [c for c in client.calls if c[0].endswith("/complete")]
    assert "LABELS" in payload["result"]["log_tail"]


def test_executor_uploads_form_images_before_completing(tmp_path):
    (tmp_path / "thumbs").mkdir()
    (tmp_path / "thumbs/a.png").write_bytes(b"\x89PNG fake")
    claude = _script(tmp_path, "claude", """
        import json, os
        path = os.path.join(os.getcwd(), ".research-hub/steps/1/result.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump({"summary": "s", "human_input": {"items": [
            {"id": "a", "title": "a", "image": "thumbs/a.png"},
            {"id": "b", "title": "b", "image": "thumbs/missing.png"},
            {"id": "c", "title": "c", "image": "../outside.png"}]}}, open(path, "w"))
        print(json.dumps({"is_error": False, "result": "ok"}))
        """)
    client = FakeClient([_step(tmp_path)])
    ex = Executor(client, tmp_path / "state", claude_bin=claude, model="opus", use_systemd=False)
    ex.tick(now=0)
    ex.wait_model(timeout=10)
    paths = [p for p, _ in client.calls]
    uploads = [payload for p, payload in client.calls if p.endswith("/media")]
    assert [u["path"] for u in uploads] == ["thumbs/a.png"] and uploads[0]["content_type"] == "image/png"
    assert paths.index("/agent/v1/steps/1/media") < paths.index("/agent/v1/steps/1/complete")


def test_sync_materializes_projects_and_uploads(tmp_path, monkeypatch):
    from agent.sync import Syncer
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "research_projects/p").mkdir(parents=True)
    (tmp_path / "research_projects/p/plan.md").write_text("old plan")

    class Hub:
        def __init__(self):
            self.results = []
            self.reported = []

        def post(self, path, payload):
            if path == "/agent/v1/sync":
                self.reported.append(payload["projects"])
                return {"projects": [{"id": 1, "slug": "p", "workdir": "~/research_projects/p"}],
                        "uploads": [{"id": 10, "project_id": 1, "path": "plan.md", "size": 8},
                                    {"id": 11, "project_id": 1, "path": "materials/a.pdf", "size": 3},
                                    {"id": 12, "project_id": 1, "path": "../evil", "size": 1}]}
            self.results.append((path, payload))
            return {}

        def get_bytes(self, path):
            return {"/agent/v1/uploads/10/content": b"new plan", "/agent/v1/uploads/11/content": b"PDF",
                    "/agent/v1/uploads/12/content": b"x"}[path]

    hub = Hub()
    Syncer(hub).run()
    root = tmp_path / "research_projects/p"
    assert (root / "plan.md").read_text() == "new plan"
    assert any(p.name.startswith("plan.md.bak-") and p.read_text() == "old plan" for p in root.iterdir())
    assert (root / "materials/a.pdf").read_bytes() == b"PDF"
    oks = {path: payload["ok"] for path, payload in hub.results}
    assert oks == {"/agent/v1/uploads/10/result": True, "/agent/v1/uploads/11/result": True,
                   "/agent/v1/uploads/12/result": False}
    Syncer(hub).run()
    assert hub.reported[-1] == {"1": {"ok": True, "error": None}}
