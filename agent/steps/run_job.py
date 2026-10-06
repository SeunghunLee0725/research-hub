"""Long-running shell jobs for `run` steps.

Jobs run detached (in their own systemd --user unit when available) so an agent restart does not
kill them. State lives in small JSON files; the exit code is written by a wrapper script."""
import json
import os
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

TAIL_BYTES = 4000
LOG_COPY_BYTES = 2_000_000


@dataclass(frozen=True)
class Job:
    step_id: int
    workdir: str
    log_path: str
    exit_path: str
    started_at: float
    timeout_s: float
    pid: int | None
    unit: str | None
    expected_artifacts: list = field(default_factory=list)
    success_marker: str | None = None
    command: str = ""


@dataclass(frozen=True)
class JobStatus:
    state: str  # running | done | timeout
    exit_code: int | None
    log_tail: str
    duration_s: float


def _jobs_dir(state_dir: Path) -> Path:
    return state_dir / "jobs"


def launch(step: dict, state_dir: Path, use_systemd: bool = True) -> Job:
    run_dir = state_dir / "runs" / str(step["id"])
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path, exit_path = run_dir / "log.txt", run_dir / "exit_code"
    exit_path.unlink(missing_ok=True)
    script = run_dir / "run.sh"
    # pipefail: a command ending in `| tee log` would otherwise report tee's success.
    script.write_text(f"#!/bin/bash\nset -o pipefail\ncd {shlex.quote(step['workdir'])} || exit 97\n"
                      f"( {step['command']}\n) > {shlex.quote(str(log_path))} 2>&1\n"
                      f"echo $? > {shlex.quote(str(exit_path))}.tmp && mv {shlex.quote(str(exit_path))}.tmp "
                      f"{shlex.quote(str(exit_path))}\n")
    script.chmod(0o700)
    unit, pid = None, None
    if use_systemd and shutil.which("systemd-run"):
        unit = f"research-hub-step-{step['id']}"
        subprocess.run(["systemd-run", "--user", "--collect", f"--unit={unit}", str(script)],
                       check=True, capture_output=True)
    else:
        pid = subprocess.Popen([str(script)], start_new_session=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL).pid
    job = Job(step["id"], step["workdir"], str(log_path), str(exit_path), time.time(),
              float(step["timeout_hours"]) * 3600, pid, unit,
              list(step.get("expected_artifacts") or []), step.get("success_marker"),
              str(step["command"]))
    _jobs_dir(state_dir).mkdir(parents=True, exist_ok=True)
    (_jobs_dir(state_dir) / f"{job.step_id}.json").write_text(json.dumps(asdict(job)))
    return job


def load_jobs(state_dir: Path) -> list[Job]:
    folder = _jobs_dir(state_dir)
    return [Job(**json.loads(p.read_text())) for p in sorted(folder.glob("*.json"))] if folder.exists() else []


def forget(job: Job, state_dir: Path) -> None:
    (_jobs_dir(state_dir) / f"{job.step_id}.json").unlink(missing_ok=True)


def _tail(path: str) -> str:
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - TAIL_BYTES))
            return stream.read().decode(errors="replace")
    except OSError:
        return ""


def poll(job: Job) -> JobStatus:
    duration = time.time() - job.started_at
    exit_path = Path(job.exit_path)
    if exit_path.exists():
        return JobStatus("done", int(exit_path.read_text().strip() or 1), _tail(job.log_path), duration)
    if duration > job.timeout_s:
        return JobStatus("timeout", None, _tail(job.log_path), duration)
    if job.pid is not None and not _alive(job.pid):
        return JobStatus("done", 98, _tail(job.log_path), duration)  # vanished without an exit code
    return JobStatus("running", None, _tail(job.log_path), duration)


def gate(job: Job, status: JobStatus) -> str | None:
    """What the step promised to leave behind. Returns the failure class, or None when it kept its word."""
    if missing_artifacts(job):
        return "missing_artifacts"
    if job.success_marker and not _log_contains(job.log_path, job.success_marker):
        return "missing_marker"
    return None


def missing_artifacts(job: Job) -> list[str]:
    return [name for name in job.expected_artifacts
            if not (Path(job.workdir) / name.rstrip("/")).exists()]


def write_record(job: Job, status: JobStatus, gate_error: str | None,
                 log_limit: int = LOG_COPY_BYTES) -> Path | None:
    """Leave the exit code, the marker check and a log copy in the work directory.
    A verify step can only recompute from files it can reach, and the real log lives in agent state."""
    folder = Path(job.workdir) / ".research-hub/steps" / str(job.step_id)
    record = {
        "step_id": job.step_id, "command": job.command, "exit_code": status.exit_code,
        "duration_s": round(status.duration_s, 1), "state": status.state,
        "expected_artifacts": list(job.expected_artifacts),
        "missing_artifacts": missing_artifacts(job),
        "success_marker": job.success_marker,
        "marker_found": None if not job.success_marker else _log_contains(job.log_path, job.success_marker),
        "gate": gate_error, "agent_log_path": job.log_path,
    }
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "run_result.json").write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n",
                                                encoding="utf-8")
        (folder / "run.log").write_bytes(_tail_bytes(job.log_path, log_limit))
    except OSError:
        return None
    return folder / "run_result.json"


def _tail_bytes(path: str, limit: int) -> bytes:
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - limit))
            return stream.read()
    except OSError:
        return b""


def _log_contains(log_path: str, needle: str) -> bool:
    try:
        with open(log_path, encoding="utf-8", errors="replace") as stream:
            return any(needle in line for line in stream)
    except OSError:
        return False


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop(job: Job) -> None:
    if job.unit:
        subprocess.run(["systemctl", "--user", "stop", job.unit], capture_output=True)
    elif job.pid is not None:
        try:
            os.killpg(job.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
