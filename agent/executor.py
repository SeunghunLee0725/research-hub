"""Leases steps from the hub and runs them: one model step at a time, plus any number of detached jobs."""
import logging
import threading
from pathlib import Path

from agent.client import HubError
from agent.steps import claude_runner, run_job

log = logging.getLogger("research-hub-agent")
PROGRESS_EVERY_S = 60


def _last_line(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:300] if lines else None


class Executor:
    def __init__(self, client, state_dir: Path, claude_bin: str, model: str, use_systemd: bool = True):
        self._client, self._state = client, state_dir
        self._claude_bin, self._model, self._use_systemd = claude_bin, model, use_systemd
        self._model_thread: threading.Thread | None = None
        self._progress_at: dict[int, float] = {}

    def _post(self, path: str, payload: dict) -> dict | None:
        try:
            return self._client.post(path, payload)
        except HubError as exc:
            log.warning("hub 요청 실패 %s: %s", path, exc)
            return None

    def _complete(self, step_id: int, status: str, result: dict | None, error_class: str | None) -> None:
        self._post(f"/agent/v1/steps/{step_id}/complete",
                   {"status": status, "result": result, "error_class": error_class})

    def _watch_jobs(self, now: float) -> None:
        for job in run_job.load_jobs(self._state):
            status = run_job.poll(job)
            if status.state == "running":
                if now - self._progress_at.get(job.step_id, 0) >= PROGRESS_EVERY_S:
                    self._post(f"/agent/v1/steps/{job.step_id}/progress", {"message": _last_line(status.log_tail)})
                    self._progress_at[job.step_id] = now
                continue
            if status.state == "timeout":
                run_job.stop(job)
                self._complete(job.step_id, "failed", {"log_tail": status.log_tail}, "timeout")
            else:
                result = {"exit_code": status.exit_code, "duration_s": round(status.duration_s, 1),
                          "log_tail": status.log_tail, "log_path": job.log_path}
                ok = status.exit_code == 0
                self._complete(job.step_id, "succeeded" if ok else "failed", result, None if ok else "exit_nonzero")
            run_job.forget(job, self._state)
            self._progress_at.pop(job.step_id, None)

    def _run_model(self, step: dict) -> None:
        stop = threading.Event()

        def heartbeat():
            while not stop.wait(PROGRESS_EVERY_S):
                self._post(f"/agent/v1/steps/{step['id']}/progress", {"message": None})

        pinger = threading.Thread(target=heartbeat, daemon=True)
        pinger.start()
        try:
            status, result, error = claude_runner.run_model_step(step, self._claude_bin, self._model)
        except Exception:  # report instead of silently dropping the step
            log.exception("model step %s crashed", step["id"])
            status, result, error = "failed", None, "tool_error"
        finally:
            stop.set()
        self._complete(step["id"], status, result, error)

    def model_busy(self) -> bool:
        return self._model_thread is not None and self._model_thread.is_alive()

    def wait_model(self, timeout: float | None = None) -> None:
        if self._model_thread is not None:
            self._model_thread.join(timeout)

    def tick(self, now: float) -> None:
        self._watch_jobs(now)
        if self.model_busy():
            return
        data = self._post("/agent/v1/lease", {})
        step = (data or {}).get("step")
        if not step:
            return
        if step["kind"] == "run":
            try:
                run_job.launch(step, self._state, use_systemd=self._use_systemd)
                self._post(f"/agent/v1/steps/{step['id']}/progress", {"message": f"실행 시작: {step['command'][:200]}"})
            except (OSError, ValueError) as exc:
                log.warning("job launch failed: %s", exc)
                self._complete(step["id"], "failed", {"message": str(exc)[:500]}, "tool_error")
            return
        self._model_thread = threading.Thread(target=self._run_model, args=(step,), daemon=True)
        self._model_thread.start()
