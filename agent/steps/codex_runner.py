"""Runs one model step with `codex exec` inside Codex's own sandbox and reads the JSON result file."""
import os
import subprocess
import tempfile
from pathlib import Path

from agent.steps.result_file import prepare, read_result

PATTERNS = (
    ("sandbox", ("bwrap:", "sandbox failed", "sandbox rejected", "landlock")),
    ("session_limit", ("usage limit", "rate limit", "quota exceeded", "hit your limit")),
    ("auth", ("401", "unauthorized", "codex login", "not logged in", "refresh token")),
    ("refusal", ("can't help with", "cannot help with", "usage policy")),
)


def build_command(codex_bin: str, model: str | None, workdir: str, sandbox: str, last_path: str) -> list[str]:
    cmd = [codex_bin, "exec"]
    if model:
        cmd += ["-m", model]
    return [*cmd, "-s", sandbox, "-C", workdir, "--skip-git-repo-check", "--ephemeral",
            "--color", "never", "-o", last_path, "-"]


def classify(returncode: int, output: str, last_message: str) -> str | None:
    text = f"{last_message}\n{output}".lower()
    for error_class, needles in PATTERNS:
        if (error_class == "sandbox" or returncode != 0) and any(n in text for n in needles):
            return error_class
    return None if returncode == 0 else "tool_error"


def run_model_step(step: dict, codex_bin: str, model: str | None, sandbox: str) -> tuple[str, dict | None, str | None]:
    result_path = prepare(step["result_path"])
    with tempfile.TemporaryDirectory(prefix="rh-codex-") as tmp:
        last_path = os.path.join(tmp, "last.txt")
        try:
            proc = subprocess.run(build_command(codex_bin, model, step["workdir"], sandbox, last_path),
                                  input=step["prompt"], capture_output=True, text=True,
                                  timeout=float(step["timeout_minutes"]) * 60)
        except subprocess.TimeoutExpired:
            return "failed", None, "timeout"
        except OSError:
            return "failed", None, "tool_error"
        last = Path(last_path).read_text() if os.path.exists(last_path) else ""
    output = (proc.stdout or "") + (proc.stderr or "")
    result = read_result(result_path)
    if result is not None and proc.returncode == 0:
        return "succeeded", {**result, "_meta": {"model": model}}, None
    error = classify(proc.returncode, output[-4000:], last[-2000:])
    if error:
        return "failed", {"message": (last or output)[-1000:]}, error
    return "failed", None, "no_result"
