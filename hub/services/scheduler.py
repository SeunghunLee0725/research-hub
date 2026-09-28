"""Advances tasks. Runs every few seconds in the scheduler process."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.db.models import Event, Project, Step, Task
from hub.services import tasks as task_service
from hub.services.pipeline import PIPELINE_MODELS, StepOutcome, next_kind, retry_decision, run_spec
from hub.services.prompts import build_prompt

SESSION_LIMIT_WAIT = timedelta(minutes=30)
MODEL_TIMEOUT_MINUTES = 120
ERROR_LABELS = {"timeout": "시간 초과", "no_result": "결과 파일 없음", "bad_result": "결과 형식 오류",
                "lost": "노드 응답 끊김", "tool_error": "실행 오류", "exit_nonzero": "명령 실패",
                "auth": "모델 로그인 문제", "refusal": "모델이 요청 거부", "session_limit": "사용량 한도"}


def _outputs(db: Session, task: Task) -> dict[str, dict]:
    steps = db.scalars(select(Step).where(Step.task_id == task.id, Step.status == "succeeded")
                       .order_by(Step.seq, Step.attempt)).all()
    return {s.kind: s.output for s in steps}


def _new_step(db: Session, task: Task, seq: int, kind: str, attempt: int, spec: dict | None) -> Step:
    project = db.get(Project, task.project_id)
    step = Step(task_id=task.id, seq=seq, kind=kind, model=PIPELINE_MODELS[kind], attempt=attempt, input={})
    db.add(step)
    db.flush()
    if kind == "run":
        step.input = {"command": spec["command"], "timeout_hours": spec["timeout_hours"],
                      "expected_artifacts": spec.get("expected_artifacts", []),
                      "success_marker": spec.get("success_marker")}
    else:
        result_path = f"{project.workdir}/.research-hub/steps/{step.id}/result.json"
        step.input = {"result_path": result_path, "timeout_minutes": MODEL_TIMEOUT_MINUTES,
                      "prompt": build_prompt(kind, project, task, _outputs(db, task), result_path)}
    return step


def _expire_leases(db: Session, now: datetime) -> None:
    for step in db.scalars(select(Step).where(Step.status.in_(("leased", "running")), Step.lease_until < now)):
        step.status, step.error_class, step.ended_at = "lost", "lost", now
        db.add(Event(task_id=step.task_id, step_id=step.id, level="warn", message=f"{step.kind} 단계 응답 끊김"))


def _start_approved(db: Session) -> None:
    for task in db.scalars(select(Task).where(Task.status == "approved")):
        task.status = "running"
        _new_step(db, task, 1, "plan", 1, None)


def _latest_step(db: Session, task: Task) -> Step | None:
    return db.scalar(select(Step).where(Step.task_id == task.id)
                     .order_by(Step.seq.desc(), Step.attempt.desc()).limit(1))


def _problem(db: Session, task: Task, step: Step) -> None:
    task.status = "problem"
    label = ERROR_LABELS.get(step.error_class, step.error_class)
    db.add(Event(task_id=task.id, step_id=step.id, level="error",
                 message=f"{step.kind} 단계 {step.attempt}회째 실패: {label}"))


def _advance(db: Session, task: Task, now: datetime) -> None:
    step = _latest_step(db, task)
    if step is None or step.status in ("pending", "leased", "running"):
        return
    if step.status == "succeeded":
        outputs = _outputs(db, task)
        following = next_kind(step.kind, step.output, outputs)
        if following is None:
            task.status, task.result_card = "review", step.output["result_card"]
            task_service.open_result_approval(db, task)
            db.add(Event(task_id=task.id, message="결과 도착 — 결과 승인 대기"))
        else:
            _new_step(db, task, step.seq + 1, following, 1, run_spec(step.kind, step.output, outputs))
        return
    decision = retry_decision(step.error_class, step.attempt)
    if decision is StepOutcome.WAIT:
        step.status, step.not_before, step.node_id = "pending", now + SESSION_LIMIT_WAIT, None
        db.add(Event(task_id=task.id, step_id=step.id, level="warn", message="사용량 한도 — 30분 뒤 재시도"))
    elif decision is StepOutcome.RETRY:
        retry = Step(task_id=task.id, seq=step.seq, kind=step.kind, model=step.model,
                     attempt=step.attempt + 1, input=step.input)
        db.add(retry)
    else:
        _problem(db, task, step)


def tick(db: Session, now: datetime) -> None:
    _expire_leases(db, now)
    _start_approved(db)
    db.flush()
    for task in db.scalars(select(Task).where(Task.status == "running")).all():
        _advance(db, task, now)
    db.commit()
