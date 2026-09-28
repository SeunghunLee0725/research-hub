"""Advances tasks. Runs every few seconds in the scheduler process."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.db.models import Event, Project, QuotaSnapshot, Step, Task
from hub.services import human, router
from hub.services import tasks as task_service
from hub.services.pipeline import StepOutcome, enforce_trust, next_kind, retry_decision, run_spec
import json

from hub.services.prompts import build_prompt, build_review_prompt

SESSION_LIMIT_WAIT = timedelta(minutes=30)
MODEL_TIMEOUT_MINUTES = 120
ERROR_LABELS = {"timeout": "시간 초과", "no_result": "결과 파일 없음", "bad_result": "결과 형식 오류",
                "lost": "노드 응답 끊김", "tool_error": "실행 오류", "exit_nonzero": "명령 실패",
                "auth": "모델 로그인 문제", "refusal": "모델이 요청 거부", "session_limit": "사용량 한도",
                "sandbox": "Codex 샌드박스 오류"}
QUOTA_FRESH = timedelta(hours=1)


def _outputs(db: Session, task: Task) -> dict[str, dict]:
    steps = db.scalars(select(Step).where(Step.task_id == task.id, Step.status == "succeeded")
                       .order_by(Step.seq, Step.attempt)).all()
    return {s.kind: s.output for s in steps}


def quota_usage(db: Session, now: datetime) -> dict[str, float | None]:
    latest = db.scalars(select(QuotaSnapshot).where(QuotaSnapshot.at >= now - QUOTA_FRESH)
                        .order_by(QuotaSnapshot.provider, QuotaSnapshot.at.desc())).all()
    windows: dict[str, list[dict]] = {}
    for snap in latest:
        windows.setdefault(snap.provider, snap.windows)
    return router.usage_from_windows(windows)


def _new_step(db: Session, task: Task, seq: int, kind: str, attempt: int, spec: dict | None,
              usage: dict[str, float | None]) -> Step:
    project = db.get(Project, task.project_id)
    step = Step(task_id=task.id, seq=seq, kind=kind, model=router.choose_model(kind, usage), attempt=attempt,
                input={})
    db.add(step)
    db.flush()
    files = human.submitted_files(db, task)
    if kind == "review":
        request = human.open_request(db, task)
        form_path = f".research-hub/steps/{step.id}/form.json"
        result_path = f"{project.workdir}/.research-hub/steps/{step.id}/result.json"
        step.input = {"result_path": result_path, "timeout_minutes": MODEL_TIMEOUT_MINUTES,
                      "files": {**files, form_path: json.dumps(request.form, ensure_ascii=False)},
                      "prompt": build_review_prompt(project, task, request.form, form_path, result_path)}
    elif kind == "run":
        step.input = {"command": spec["command"], "timeout_hours": spec["timeout_hours"],
                      "expected_artifacts": spec.get("expected_artifacts", []),
                      "success_marker": spec.get("success_marker"), "files": files}
    else:
        result_path = f"{project.workdir}/.research-hub/steps/{step.id}/result.json"
        step.input = {"result_path": result_path, "timeout_minutes": MODEL_TIMEOUT_MINUTES, "files": files,
                      "prompt": build_prompt(kind, project, task, _outputs(db, task), result_path)}
    return step


def _expire_leases(db: Session, now: datetime) -> None:
    for step in db.scalars(select(Step).where(Step.status.in_(("leased", "running")), Step.lease_until < now)):
        step.status, step.error_class, step.ended_at = "lost", "lost", now
        db.add(Event(task_id=step.task_id, step_id=step.id, level="warn", message=f"{step.kind} 단계 응답 끊김"))


def _start_approved(db: Session, usage: dict[str, float | None]) -> None:
    for task in db.scalars(select(Task).where(Task.status == "approved")):
        task.status = "running"
        _new_step(db, task, 1, "plan", 1, None, usage)


def _latest_step(db: Session, task: Task) -> Step | None:
    return db.scalar(select(Step).where(Step.task_id == task.id)
                     .order_by(Step.seq.desc(), Step.attempt.desc()).limit(1))


def _problem(db: Session, task: Task, step: Step) -> None:
    task.status = "problem"
    label = ERROR_LABELS.get(step.error_class, step.error_class)
    db.add(Event(task_id=task.id, step_id=step.id, level="error",
                 message=f"{step.kind} 단계 {step.attempt}회째 실패: {label}"))


def _finish_review(db: Session, task: Task, step: Step, now: datetime, usage: dict[str, float | None]) -> bool:
    """Accept the LLM's answers and continue the pipeline; on invalid answers mark the step failed (retried)."""
    request = human.open_request(db, task)
    try:
        answers = human.validate_answers(request.form, step.output["answers"])
    except ValueError as exc:
        step.status, step.error_class = "failed", "bad_result"
        db.add(Event(task_id=task.id, step_id=step.id, level="warn", message=f"LLM 판정 형식 오류: {exc}"[:500]))
        return False
    human.accept_ai_answers(db, request, answers, now)
    requester = db.get(Step, request.step_id)
    outputs = _outputs(db, task)
    following = next_kind(requester.kind, requester.output, outputs)
    if following is not None:
        _new_step(db, task, step.seq + 1, following, 1, run_spec(requester.kind, requester.output, outputs), usage)
    return True


def _switch_model(db: Session, task: Task, step: Step) -> bool:
    """On model-specific failures, retry the same step once with the other model."""
    other = router.alternate(step.model)
    if step.error_class not in router.SWITCHABLE or other is None:
        return False
    tried = set(db.scalars(select(Step.model).where(Step.task_id == task.id, Step.seq == step.seq)))
    if other in tried:
        return False
    db.add(Step(task_id=task.id, seq=step.seq, kind=step.kind, model=other, attempt=step.attempt + 1,
                input=step.input))
    label = ERROR_LABELS.get(step.error_class, step.error_class)
    db.add(Event(task_id=task.id, step_id=step.id, level="warn",
                 message=f"{step.kind} 단계 {step.model} {label} → {other}로 전환"))
    return True


def _advance(db: Session, task: Task, now: datetime, usage: dict[str, float | None]) -> None:
    step = _latest_step(db, task)
    if step is None or step.status in ("pending", "leased", "running"):
        return
    if step.status == "succeeded" and step.kind == "review" and _finish_review(db, task, step, now, usage):
        return
    if step.status == "succeeded":
        form = (step.output or {}).get("human_input")
        request = human.request_for_step(db, step)
        if form and request is None:
            project = db.get(Project, task.project_id)
            by_llm = task.review_by == "llm" or (task.review_by is None and project.auto_ai_review)
            request = human.create_request(db, task, step, form, by_llm=by_llm)
        if request is not None and request.status == "pending":
            if request.answered_by == "llm":
                _new_step(db, task, step.seq + 1, "review", 1, None, usage)
            return
        outputs = _outputs(db, task)
        following = next_kind(step.kind, step.output, outputs)
        if following is None:
            task.status = "review"
            task.result_card = enforce_trust(step.output["result_card"], outputs.get("verify"))
            task_service.open_result_approval(db, task)
            db.add(Event(task_id=task.id, message="결과 도착 — 결과 승인 대기"))
        else:
            _new_step(db, task, step.seq + 1, following, 1, run_spec(step.kind, step.output, outputs), usage)
        return
    if _switch_model(db, task, step):
        return
    decision = retry_decision(step.error_class, step.attempt, step.kind)
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
    usage = quota_usage(db, now)
    _start_approved(db, usage)
    db.flush()
    for task in db.scalars(select(Task).where(Task.status == "running")).all():
        _advance(db, task, now, usage)
    db.commit()
