import json
import stat
import sys
import textwrap

from agent.steps import codex_runner


def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _step(tmp_path, **extra):
    return {"id": 3, "kind": "verify", "model": "codex", "workdir": str(tmp_path), "prompt": "check",
            "result_path": str(tmp_path / ".research-hub/steps/3/result.json"), "timeout_minutes": 1, **extra}


def test_build_command_uses_sandbox_and_model(tmp_path):
    cmd = codex_runner.build_command("/bin/codex", "gpt-5.6-sol", "/w", "workspace-write", "/tmp/last")
    assert cmd[:2] == ["/bin/codex", "exec"]
    assert ["-m", "gpt-5.6-sol"] == cmd[cmd.index("-m"):cmd.index("-m") + 2]
    assert ["-s", "workspace-write"] == cmd[cmd.index("-s"):cmd.index("-s") + 2]
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd and cmd[-1] == "-"
    assert "-m" not in codex_runner.build_command("/bin/codex", None, "/w", "workspace-write", "/tmp/l")


def test_classify():
    assert codex_runner.classify(0, "", "done") is None
    assert codex_runner.classify(0, "", "sandbox failed (`bwrap: setting up uid map: Permission denied`)") == "sandbox"
    assert codex_runner.classify(1, "ERROR: You've hit your usage limit", "") == "session_limit"
    assert codex_runner.classify(1, "401 Unauthorized, please run codex login", "") == "auth"
    assert codex_runner.classify(1, "boom", "") == "tool_error"


def test_run_model_step_success(tmp_path):
    codex = _script(tmp_path, "codex", """
        import json, os, sys
        args = sys.argv
        cwd = args[args.index("-C") + 1]
        last = args[args.index("-o") + 1]
        prompt = sys.stdin.read()
        path = os.path.join(cwd, ".research-hub/steps/3/result.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump({"verified": True, "checks": [], "echo": prompt}, open(path, "w"))
        open(last, "w").write("done")
        """)
    status, result, error = codex_runner.run_model_step(_step(tmp_path), codex, "m", "workspace-write")
    assert (status, error) == ("succeeded", None) and result["echo"] == "check"


def test_run_model_step_sandbox_failure(tmp_path):
    codex = _script(tmp_path, "codex", """
        import sys
        last = sys.argv[sys.argv.index("-o") + 1]
        open(last, "w").write("Unable: bwrap: loopback: Failed RTM_NEWADDR")
        """)
    assert codex_runner.run_model_step(_step(tmp_path), codex, None, "workspace-write")[2] == "sandbox"


def test_run_model_step_missing_result(tmp_path):
    codex = _script(tmp_path, "codex", """
        import sys
        open(sys.argv[sys.argv.index("-o") + 1], "w").write("ok")
        """)
    assert codex_runner.run_model_step(_step(tmp_path), codex, None, "workspace-write")[2] == "no_result"
