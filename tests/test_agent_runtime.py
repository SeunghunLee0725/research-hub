import json

import pytest

from agent.client import HubClient, HubError
from agent.config import load_config
from agent.schedule import Schedule


class FakeResponse:
    def __init__(self, body, status=200):
        self._body, self.status = body, status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_client_posts_json_with_bearer_token():
    seen = {}

    def opener(req, timeout):
        seen.update(url=req.full_url, auth=req.get_header("Authorization"),
                    body=json.loads(req.data), timeout=timeout)
        return FakeResponse(b'{"success": true, "data": {"node": "a"}, "error": null}')

    client = HubClient("http://hub:8020/", "tok", opener=opener)
    assert client.post("/agent/v1/heartbeat", {"x": 1}) == {"node": "a"}
    assert seen == {"url": "http://hub:8020/agent/v1/heartbeat", "auth": "Bearer tok",
                    "body": {"x": 1}, "timeout": 15}


def test_client_raises_on_error_envelope():
    client = HubClient("http://hub", "t", opener=lambda r, timeout: FakeResponse(
        b'{"success": false, "data": null, "error": "bad"}'))
    with pytest.raises(HubError, match="bad"):
        client.post("/x", {})


def test_client_wraps_transport_errors():
    def boom(req, timeout):
        raise OSError("refused")
    with pytest.raises(HubError, match="refused"):
        HubClient("http://hub", "t", opener=boom).post("/x", {})


def test_schedule_runs_each_job_when_due():
    schedule = Schedule({"heartbeat": 30, "quota": 600})
    assert schedule.due(0) == ("heartbeat", "quota")
    schedule = schedule.mark(("heartbeat", "quota"), 0)
    assert schedule.due(29) == ()
    assert schedule.due(30) == ("heartbeat",)
    schedule = schedule.mark(("heartbeat",), 30)
    assert schedule.due(600) == ("heartbeat", "quota")


def test_load_config(tmp_path):
    env = tmp_path / "agent.env"
    env.write_text("# c\nHUB_URL=http://100.1.1.1:8020\nHUB_NODE_TOKEN=abc\n")
    cfg = load_config(env)
    assert (cfg.hub_url, cfg.token, cfg.disk_path) == ("http://100.1.1.1:8020", "abc", "/")


def test_load_config_requires_values(tmp_path):
    env = tmp_path / "agent.env"
    env.write_text("HUB_URL=http://x\n")
    with pytest.raises(ValueError, match="HUB_NODE_TOKEN"):
        load_config(env)


def test_load_config_execution_options(tmp_path):
    env = tmp_path / "agent.env"
    env.write_text("HUB_URL=http://x\nHUB_NODE_TOKEN=t\nAGENT_EXECUTE=0\nAGENT_MODEL=sonnet\n")
    cfg = load_config(env)
    assert (cfg.execute, cfg.model) == (False, "sonnet")
    assert load_config(tmp_path.joinpath("agent.env")).execute is False


def test_load_config_codex_options(tmp_path):
    env = tmp_path / "agent.env"
    env.write_text("HUB_URL=http://x\nHUB_NODE_TOKEN=t\nAGENT_CODEX_BIN=/opt/codex\nAGENT_CODEX_MODEL=gpt-5.6-sol\n"
                   "AGENT_CLAUDE_MODEL=opus\n")
    cfg = load_config(env)
    assert (cfg.codex_bin, cfg.codex_model, cfg.codex_sandbox, cfg.model) == \
        ("/opt/codex", "gpt-5.6-sol", "workspace-write", "opus")
