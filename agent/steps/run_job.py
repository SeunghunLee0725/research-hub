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
from dataclasses import asdict, dataclass
from pathlib import Path

TAIL_BYTES = 4000


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
    script.write_text(f"#!/bin/bash\ncd {shlex.quote(step['workdir'])} || exit 97\n"
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
              float(step["timeout_hours"]) * 3600, pid, unit)
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
