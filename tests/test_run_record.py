"""A run step leaves its own evidence in the work directory so a later verify step can read it."""
import json
import time

from agent.steps import run_job


def _wait(job, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        status = run_job.poll(job)
        if status.state != "running":
            return status
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def _launch(tmp_path, command, **extra):
    step = {"id": 42, "workdir": str(tmp_path), "command": command, "timeout_hours": 1, **extra}
    return run_job.launch(step, tmp_path / "state", use_systemd=False)


def test_the_record_holds_the_exit_code_marker_and_artifacts(tmp_path):
    job = _launch(tmp_path, "touch report.csv; echo STAGE1_OK",
                  expected_artifacts=["report.csv", "absent.json"], success_marker="STAGE1_OK")
    status = _wait(job)
    path = run_job.write_record(job, status, run_job.gate(job, status))

    assert path == tmp_path / ".research-hub/steps/42/run_result.json"
    record = json.loads(path.read_text())
    assert record["exit_code"] == 0
    assert record["command"] == "touch report.csv; echo STAGE1_OK"
    assert record["marker_found"] is True
    assert record["missing_artifacts"] == ["absent.json"]
    assert record["gate"] == "missing_artifacts"


def test_the_log_copy_sits_next_to_the_record_and_is_capped(tmp_path):
    job = _launch(tmp_path, "python3 -c \"print('x' * 200)\" ")
    status = _wait(job)
    run_job.write_record(job, status, None, log_limit=64)

    copy = tmp_path / ".research-hub/steps/42/run.log"
    assert copy.exists() and copy.stat().st_size <= 64


def test_a_record_outside_the_work_directory_is_not_written(tmp_path):
    job = _launch(tmp_path, "echo hi")
    status = _wait(job)
    broken = run_job.Job(**{**job.__dict__, "workdir": "/nonexistent-work-dir"})
    assert run_job.write_record(broken, status, None) is None
