import base64
import json
from pathlib import Path

from agent.probes.run import run


def _login(logged_in, auth_method=None, subscription=None, error=None) -> dict:
    return {"logged_in": logged_in, "auth_method": auth_method, "subscription": subscription, "error": error}


def parse_claude_status(stdout: str, returncode: int) -> dict:
    # Only non-identifying fields leave the machine: no email, org id or tokens.
    try:
        data = json.loads(stdout)
    except ValueError:
        return _login(None, error=f"claude auth status 해석 실패 (rc={returncode})")
    return _login(bool(data.get("loggedIn")), data.get("authMethod"), data.get("subscriptionType"))


def parse_codex_status(stdout: str, returncode: int) -> dict:
    text = stdout.strip().lower()
    if text.startswith("logged in"):
        method = "chatgpt" if "chatgpt" in text else "api_key" if "api key" in text else None
        return _login(True, method)
    if "not logged in" in text:
        return _login(False)
    return _login(None, error=f"codex login status 해석 실패 (rc={returncode})")


def codex_plan(auth: dict) -> str | None:
    try:
        payload = auth["tokens"]["id_token"].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return claims["https://api.openai.com/auth"]["chatgpt_plan_type"]
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return None


def collect(claude_bin: str, codex_bin: str, home: Path) -> dict:
    rc, out = run([claude_bin, "auth", "status"])
    claude = parse_claude_status(out, rc) if rc != 127 else _login(None, error="claude 미설치")
    rc, out = run([codex_bin, "login", "status"])
    codex = parse_codex_status(out, rc) if rc != 127 else _login(None, error="codex 미설치")
    if codex["logged_in"]:
        try:
            codex = {**codex, "subscription": codex_plan(json.loads((home / ".codex/auth.json").read_text()))}
        except (OSError, ValueError):
            pass
    return {"claude": claude, "codex": codex}
