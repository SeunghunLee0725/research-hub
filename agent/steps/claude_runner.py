"""Runs one model step with the Claude Code CLI and reads the step's JSON result file."""
import json
import os
import subprocess
from pathlib import Path

from agent.steps.result_file import prepare, read_result

ALLOWED_TOOLS = "Bash,Read,Edit,Write,Glob,Grep,WebSearch,WebFetch"
PATTERNS = (
    ("session_limit", ("session limit", "usage limit", "hit your limit", "rate limit")),
    ("auth", ("/login", "not logged in", "invalid api key", "authentication", "oauth token")),
    ("refusal", ("can't help with", "cannot help with", "general_harms", "usage policy")),
)


def build_command(claude_bin: str, model: str) -> list[str]:
    return [claude_bin, "-p", "--output-format", "json", "--model", model,
            "--permission-mode", "acceptEdits", "--allowedTools", ALLOWED_TOOLS]


def _parse(stdout: str) -> dict | None:
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def classify(returncode: int, stdout: str, stderr: str) -> str | None:
    data = _parse(stdout)
    if returncode == 0 and data is not None and not data.get("is_error"):
        return None
    text = " ".join([str((data or {}).get("result", "")), stdout if data is None else "", stderr]).lower()
    for error_class, needles in PATTERNS:
        if any(n in text for n in needles):
            return error_class
    return "tool_error"


def model_name(meta: dict) -> str | None:
    """The CLI reports the model it actually used under modelUsage."""
    usage = meta.get("modelUsage")
    if not isinstance(usage, dict) or not usage:
        return None
    first = next(iter(usage.values()))
    canonical = first.get("canonicalModel") if isinstance(first, dict) else None
    return str(canonical or next(iter(usage)))


def run_model_step(step: dict, claude_bin: str, model: str) -> tuple[str, dict | None, str | None]:
    result_path = prepare(step["result_path"])
    try:
        proc = subprocess.run(build_command(claude_bin, model), input=step["prompt"], cwd=step["workdir"],
                              capture_output=True, text=True, timeout=float(step["timeout_minutes"]) * 60,
                              env={**os.environ, "RESEARCH_HUB_STEP": str(step["id"])})
    except subprocess.TimeoutExpired:
        return "failed", None, "timeout"
    except OSError:
        return "failed", None, "tool_error"
    error = classify(proc.returncode, proc.stdout, proc.stderr)
    if error:
        return "failed", {"message": (proc.stdout or proc.stderr)[-1000:]}, error
    result = read_result(result_path)
    if result is None:
        return "failed", None, "no_result"
    meta = _parse(proc.stdout) or {}
    return "succeeded", {**result, "_meta": {"cost_usd": meta.get("total_cost_usd"),
                                             "session_id": meta.get("session_id"),
                                             "model": model_name(meta)}}, None
