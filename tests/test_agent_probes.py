import base64
import json

import pytest

from agent.probes.gpu import parse_gpu_csv, parse_gpu_procs
from agent.probes.logins import codex_plan, parse_claude_status, parse_codex_status
from agent.probes.quota import TokenUnavailable, claude_access_token, claude_windows, codex_windows
from agent.probes.system import parse_meminfo


def test_parse_gpu_csv_handles_unified_memory():
    text = "NVIDIA GB10, 61, [N/A], [N/A]\nNVIDIA A4000, 5, 1024, 16376\n"
    assert parse_gpu_csv(text) == [
        {"name": "NVIDIA GB10", "util_pct": 61, "mem_used_mb": None, "mem_total_mb": None},
        {"name": "NVIDIA A4000", "util_pct": 5, "mem_used_mb": 1024, "mem_total_mb": 16376},
    ]


def test_parse_gpu_csv_ignores_garbage_lines():
    assert parse_gpu_csv("\nNo devices were found\n") == []


def test_parse_gpu_procs():
    text = "1234, /usr/bin/python3, 2048\n99, ollama, [N/A]\n"
    assert parse_gpu_procs(text) == [
        {"pid": 1234, "name": "python3", "mem_mb": 2048},
        {"pid": 99, "name": "ollama", "mem_mb": None},
    ]


def test_parse_meminfo():
    text = "MemTotal:       125829120 kB\nMemFree: 1 kB\nMemAvailable:    94371840 kB\n"
    assert parse_meminfo(text) == (122880, 30720)


def test_parse_claude_status_logged_in_drops_identity_fields():
    out = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "a@b.c",
                      "orgId": "x", "subscriptionType": "max"})
    assert parse_claude_status(out, 0) == {
        "logged_in": True, "auth_method": "claude.ai", "subscription": "max", "error": None}


def test_parse_claude_status_logged_out_and_errors():
    assert parse_claude_status(json.dumps({"loggedIn": False}), 1)["logged_in"] is False
    bad = parse_claude_status("not json", 0)
    assert bad["logged_in"] is None and bad["error"]


def test_parse_codex_status():
    assert parse_codex_status("Logged in using ChatGPT\n", 0)["logged_in"] is True
    assert parse_codex_status("Logged in using ChatGPT\n", 0)["auth_method"] == "chatgpt"
    assert parse_codex_status("Not logged in\n", 1)["logged_in"] is False


def _jwt(claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def test_codex_plan_from_id_token():
    auth = {"tokens": {"id_token": _jwt({"https://api.openai.com/auth": {"chatgpt_plan_type": "pro"}})}}
    assert codex_plan(auth) == "pro"
    assert codex_plan({}) is None
    assert codex_plan({"tokens": {"id_token": "garbage"}}) is None


def test_claude_access_token_requires_unexpired_token():
    creds = {"claudeAiOauth": {"accessToken": "tok", "expiresAt": 2_000}}
    assert claude_access_token(creds, now_ms=1_000) == "tok"
    with pytest.raises(TokenUnavailable):
        claude_access_token(creds, now_ms=3_000)
    with pytest.raises(TokenUnavailable):
        claude_access_token({"claudeAiOauth": {"accessToken": "", "expiresAt": 0}}, now_ms=1)
    with pytest.raises(TokenUnavailable):
        claude_access_token({}, now_ms=1)


def test_claude_windows():
    data = {"five_hour": {"utilization": 58.0, "resets_at": "2026-09-28T05:00:00Z"},
            "seven_day": {"utilization": 41, "resets_at": None}, "seven_day_opus": None}
    rows = claude_windows(data)
    assert [(r["id"], r["label"], r["used_percent"]) for r in rows] == [
        ("five_hour", "5시간", 58.0), ("seven_day", "7일", 41)]
    assert rows[0]["resets_at"] == "2026-09-28T05:00:00+00:00"


def test_claude_windows_drops_out_of_range():
    assert claude_windows({"five_hour": {"utilization": 250}}) == []


def test_codex_windows():
    data = {"rateLimits": {"primary": {"usedPercent": 20, "windowDurationMins": 300, "resetsAt": 1790000000},
                           "secondary": {"usedPercent": 7, "windowDurationMins": 10080, "resetsAt": None}}}
    rows = codex_windows(data)
    assert [(r["id"], r["label"], r["used_percent"]) for r in rows] == [
        ("primary", "5시간", 20), ("secondary", "7일", 7)]
