"""A run step only succeeds when the pipeline succeeded AND it left what it promised."""
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
    step = {"id": 1, "workdir": str(tmp_path), "command": command, "timeout_hours": 1, **extra}
    return run_job.launch(step, tmp_path / "state", use_systemd=False)


def test_a_failing_command_piped_into_tee_is_not_a_success(tmp_path):
    """`... | tee log` returns tee's code, so the wrapper needs pipefail."""
    job = _launch(tmp_path, "sh -c 'echo boom; exit 3' | tee out.log")
    assert _wait(job).exit_code == 3


def test_missing_artifacts_fail_the_step(tmp_path):
    job = _launch(tmp_path, "echo done", expected_artifacts=["report.csv", "results/"])
    status = _wait(job)
    assert status.exit_code == 0
    assert run_job.gate(job, status) == "missing_artifacts"


def test_a_missing_success_marker_fails_the_step(tmp_path):
    job = _launch(tmp_path, "echo half-way", success_marker="STAGE1_OK")
    assert run_job.gate(job, _wait(job)) == "missing_marker"


def test_declared_artifacts_and_marker_together_pass(tmp_path):
    (tmp_path / "results").mkdir()
    job = _launch(tmp_path, "touch report.csv; echo STAGE1_OK",
                  expected_artifacts=["report.csv", "results/"], success_marker="STAGE1_OK")
    status = _wait(job)
    assert status.exit_code == 0 and run_job.gate(job, status) is None


def test_a_step_that_declares_nothing_still_passes(tmp_path):
    job = _launch(tmp_path, "echo hi")
    assert run_job.gate(job, _wait(job)) is None
