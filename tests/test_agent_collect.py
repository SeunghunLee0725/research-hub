import json
import stat
import sys
import textwrap

from agent.probes import logins, quota, system
from agent.probes.run import run


def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_run_handles_missing_binary_and_stderr(tmp_path):
    assert run(["/nonexistent/bin"]) == (127, "")
    err = _script(tmp_path, "err", "import sys; sys.stderr.write('Logged in using ChatGPT\\n')")
    assert run([err]) == (0, "Logged in using ChatGPT\n")


def test_system_collect_reports_disk():
    metrics = system.collect("/")
    assert metrics["disk_total_gb"] > 0 and len(metrics["load"]) == 3


def test_logins_collect_with_fake_clis(tmp_path):
    claude = _script(tmp_path, "claude", """
        import json; print(json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                                       "subscriptionType": "max", "email": "x@y"}))""")
    codex = _script(tmp_path, "codex", "print('Logged in using ChatGPT')")
    result = logins.collect(claude, codex, tmp_path)
    assert result["claude"]["subscription"] == "max"
    assert result["codex"]["logged_in"] is True and result["codex"]["subscription"] is None
    assert "email" not in json.dumps(result)


def test_logins_collect_reports_missing_clis(tmp_path):
    result = logins.collect("/nope/claude", "/nope/codex", tmp_path)
    assert result["claude"]["error"] == "claude 미설치" and result["codex"]["error"] == "codex 미설치"


def test_codex_usage_over_fake_app_server(tmp_path):
    codex = _script(tmp_path, "codex", """
        import json, sys
        for line in sys.stdin:
            msg = json.loads(line)
            if msg.get("method") == "initialize":
                print(json.dumps({"id": msg["id"], "result": {}}), flush=True)
            elif msg.get("method") == "account/rateLimits/read":
                print(json.dumps({"id": msg["id"], "result": {"rateLimits": {
                    "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": None}}}}), flush=True)
        """)
    result = quota.codex_usage(codex)
    assert result["windows"][0]["used_percent"] == 12 and result["error"] is None


def test_safe_reports_errors_instead_of_raising(tmp_path):
    assert quota.safe("claude", lambda: quota.claude_usage(tmp_path))["error"].startswith("FileNotFoundError")

    def expired():
        raise quota.TokenUnavailable("Claude 토큰 만료")
    assert quota.safe("claude", expired) == {"provider": "claude", "windows": [], "error": "Claude 토큰 만료"}


def test_period_label():
    assert [quota.period_label(m) for m in (None, 300, 10080, 90)] == ["한도", "5시간", "7일", "90분"]
