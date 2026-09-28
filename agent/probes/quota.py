"""Subscription usage probes. They only read credentials; they never refresh or rewrite them."""
import json
import math
import os
import selectors
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_WINDOWS = {"five_hour": ("5시간", 300), "seven_day": ("7일", 10080),
                  "seven_day_opus": ("Opus 7일", 10080), "seven_day_sonnet": ("Sonnet 7일", 10080)}
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


class TokenUnavailable(RuntimeError):
    pass


def _iso(value) -> str | None:
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _valid_pct(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100


def period_label(minutes: int | None) -> str:
    if not minutes:
        return "한도"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}일"
    return f"{minutes // 60}시간" if minutes % 60 == 0 else f"{minutes}분"


def claude_access_token(creds: dict, now_ms: int) -> str:
    oauth = creds.get("claudeAiOauth") or {}
    token, expires = oauth.get("accessToken"), oauth.get("expiresAt") or 0
    if not token:
        raise TokenUnavailable("Claude 로그인 토큰 없음")
    if expires <= now_ms:
        raise TokenUnavailable("Claude 토큰 만료 — 다음 Claude 실행 때 자동 갱신")
    return token


def claude_windows(data: dict) -> list[dict]:
    rows = []
    for key, (label, minutes) in CLAUDE_WINDOWS.items():
        value = data.get(key)
        if isinstance(value, dict) and _valid_pct(value.get("utilization")):
            rows.append({"id": key, "label": label, "used_percent": value["utilization"],
                         "resets_at": _iso(value.get("resets_at")), "window_minutes": minutes})
    return rows


def codex_windows(data: dict) -> list[dict]:
    limits = data.get("rateLimits") if isinstance(data, dict) else None
    rows = []
    for period in ("primary", "secondary"):
        value = (limits or {}).get(period)
        if isinstance(value, dict) and _valid_pct(value.get("usedPercent")):
            minutes = value.get("windowDurationMins")
            rows.append({"id": period, "label": period_label(minutes), "used_percent": value["usedPercent"],
                         "resets_at": _iso(value.get("resetsAt")), "window_minutes": minutes})
    return rows


def claude_usage(home: Path) -> dict:
    creds = json.loads((home / ".claude/.credentials.json").read_text())
    token = claude_access_token(creds, int(time.time() * 1000))
    req = urllib.request.Request(USAGE_URL, headers={"Authorization": f"Bearer {token}",
                                                     "anthropic-beta": "oauth-2025-04-20"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return {"provider": "claude", "windows": claude_windows(json.loads(resp.read())), "error": None}


def _rpc(proc, request: dict, timeout: float = 20) -> dict:
    proc.stdin.write((json.dumps(request) + "\n").encode())
    proc.stdin.flush()
    deadline, buffer = time.monotonic() + timeout, b""
    os.set_blocking(proc.stdout.fileno(), False)
    with selectors.DefaultSelector() as sel:
        sel.register(proc.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline and sel.select(max(0.0, deadline - time.monotonic())):
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                message = json.loads(line)
                if message.get("id") == request["id"]:
                    if "error" in message:
                        raise RuntimeError(f"codex RPC 오류: {message['error']}")
                    return message["result"]
    raise TimeoutError("codex RPC 응답 없음")


def codex_usage(codex_bin: str) -> dict:
    proc = subprocess.Popen([codex_bin, "app-server", "--listen", "stdio://"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    try:
        _rpc(proc, {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "research_hub", "version": "0.1"}}})
        proc.stdin.write(b'{"method":"initialized"}\n')
        proc.stdin.flush()
        data = _rpc(proc, {"id": 2, "method": "account/rateLimits/read"})
        return {"provider": "codex", "windows": codex_windows(data), "error": None}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def safe(provider: str, probe) -> dict:
    try:
        return probe()
    except TokenUnavailable as exc:
        return {"provider": provider, "windows": [], "error": str(exc)}
    except Exception as exc:  # report any probe failure to the hub instead of crashing the agent
        return {"provider": provider, "windows": [], "error": f"{type(exc).__name__}: {exc}"[:300]}
