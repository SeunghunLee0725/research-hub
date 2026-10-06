"""Advances tasks. Runs every few seconds in the scheduler process."""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.db.models import Approval, Event, HumanRequest, Project, QuotaSnapshot, Step, Task
from hub.services import human, router
from hub.services import tasks as task_service
from hub.services.pipeline import StepOutcome, enforce_trust, next_kind, retry_decision, run_spec
import json

from hub.services.prompts import build_prompt, build_review_prompt

log = logging.getLogger("research-hub")
SESSION_LIMIT_WAIT = timedelta(minutes=30)
MODEL_TIMEOUT_MINUTES = 120
ERROR_LABELS = {"timeout": "시간 초과", "no_result": "결과 파일 없음", "bad_result": "결과 형식 오류",
                "lost": "노드 응답 끊김", "tool_error": "실행 오류", "exit_nonzero": "명령 실패",
                "auth": "모델 로그인 문제", "refusal": "모델이 요청 거부", "session_limit": "사용량 한도",
               "missing_artifacts": "약속한 산출물 없음", "missing_marker": "성공 표시 없음",
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
                      "prompt": build_review_prompt(project, task, request.form, form_path, result_path,
                                                    draft=request.answered_by == "draft")}
    elif kind == "run":
        step.input = {"command": spec["command"], "timeout_hours": spec["timeout_hours"],
                      "expected_artifacts": spec.get("expected_artifacts", []),
                      "success_marker": spec.get("success_marker"), "files": files}
    else:
        result_path = f"{project.workdir}/.research-hub/steps/{step.id}/result.json"
        step.input = {"result_path": result_path, "timeout_minutes": MODEL_TIMEOUT_MINUTES, "files": files,
                      "prompt": build_prompt(kind, project, task, _outputs(db, task), result_path,
                                             _failures(db, task) if kind == "diagnose" else None)}
    return step


def _expire_leases(db: Session, now: datetime) -> None:
    for step in db.scalars(select(Step).where(Step.status.in_(("leased", "running")), Step.lease_until < now)):
        step.status, step.error_class, step.ended_at = "lost", "lost", now
        db.add(Event(task_id=step.task_id, step_id=step.id, level="warn", message=f"{step.kind} 단계 응답 끊김"))


def _start_approved(db: Session, usage: dict[str, float | None]) -> None:
    for task in db.scalars(select(Task).where(Task.status == "approved")):
        task.status = "running"
        _new_step(db, task, 1, "plan", 1, None, usage)


def _guarded(db: Session, task: Task, what: str, action) -> None:
    """One task's problem is that task's problem: record it and let the other projects go on."""
    try:
        action()
    except Exception as exc:  # noqa: BLE001 - a scheduler tick must survive one bad task
        db.rollback()
        log.exception("%s 실패 (작업 %s)", what, task.id)
        db.add(Event(task_id=task.id, level="error", message=f"{what} 실패: {exc}"[:500]))
        db.commit()


def _auto_start_proposed(db: Session, now: datetime) -> None:
    """A proposed task does not wait for the researcher when the project lets the agent decide."""
    for task in db.scalars(select(Task).where(Task.status == "proposed")).all():
        if not db.get(Project, task.project_id).auto_approve:
            continue
        _guarded(db, task, "자동 시작 승인", lambda t=task: task_service.approve_start(db, t, now,
                                                                                 decided_by="agent:auto"))


def _decide_result_gates(db: Session, now: datetime, usage: dict[str, float | None]) -> None:
    """Put a review-gate task in front of the agent, unless a step is already doing that."""
    for task in db.scalars(select(Task).where(Task.status == "review")).all():
        if not db.get(Project, task.project_id).auto_approve:
            continue
        approval = task_service.pending_approval(db, task)
        if approval is None or approval.kind != "result":
            continue
        latest = _latest_step(db, task)
        if latest is not None and latest.status in ("pending", "leased", "running"):
            continue
        if latest is not None and latest.kind == "approve":
            # A task at its gate is not "running", so _advance never sees it; apply the decision here.
            if latest.status == "succeeded":
                _apply_agent_approval(db, task, latest, now, usage)
            continue
        _new_step(db, task, (latest.seq if latest else 0) + 1, "approve", 1, None, usage)


def _failures(db: Session, task: Task) -> list[dict]:
    """What a diagnose step needs: the steps that failed, with the command and the tail of their log."""
    rows = db.scalars(select(Step).where(Step.task_id == task.id, Step.status == "failed")
                      .order_by(Step.seq, Step.attempt)).all()
    out = []
    for step in rows[-5:]:
        result = step.output or {}
        out.append({"step_id": step.id, "kind": step.kind, "attempt": step.attempt,
                    "error": ERROR_LABELS.get(step.error_class, step.error_class),
                    "command": (step.input or {}).get("command"),
                    "exit_code": result.get("exit_code"),
                    "log_path": result.get("log_path"),
                    "log_tail": (result.get("log_tail") or "")[-3000:],
                    "message": str(result.get("message") or "")[:1000],
                    "missing_artifacts": result.get("missing")})
    return out


def _diagnose_problems(db: Session, now: datetime, usage: dict[str, float | None]) -> None:
    """A task that stopped should say why. One diagnosis per problem, then it waits for the researcher."""
    for task in db.scalars(select(Task).where(Task.status == "problem")).all():
        if not db.get(Project, task.project_id).auto_diagnose:
            continue
        if task_service.pending_approval(db, task) is not None:
            continue
        latest = _latest_step(db, task)
        if latest is None or latest.status in ("pending", "leased", "running"):
            continue
        if latest.kind == "diagnose":
            if latest.status == "succeeded":
                _open_fix_approval(db, task, latest, now)
            continue
        if db.scalar(select(Step).where(Step.task_id == task.id, Step.kind == "diagnose",
                                        Step.seq > latest.seq - 1).limit(1)) is not None:
            continue
        _guarded(db, task, "진단 단계 생성",
                 lambda t=task, s=latest: _new_step(db, t, s.seq + 1, "diagnose", 1, None, usage))


def _open_fix_approval(db: Session, task: Task, step: Step, now: datetime) -> None:
    result = step.output or {}
    db.add(Approval(task_id=task.id, kind="fix", status="pending",
                    choice={**{k: result.get(k) for k in ("cause", "fix", "confidence", "risk",
                                                          "retry_after_fix")},
                            "diagnosed_by": f"agent:{_model_label(step)}", "step_id": step.id}))
    db.add(Event(task_id=task.id, step_id=step.id,
                 message=f"원인: {str(result.get('cause'))[:300]} — 제안대로 고칠지 연구자 확인 대기"))


def _apply_agent_approval(db: Session, task: Task, step: Step, now: datetime,
                          usage: dict[str, float | None]) -> None:
    result = step.output or {}
    if result.get("decision") != "approved":
        db.add(Event(task_id=task.id, step_id=step.id, level="warn",
                     message=f"에이전트가 결과 승인을 보류했다: {str(result.get('reason'))[:300]}"))
        return
    options = (task.result_card or {}).get("next_options", [])
    index = result.get("next_option")
    if index is not None and not 0 <= index < len(options):
        index = None
    task_service.approve_result(db, task, index, now, decided_by=f"agent:{_model_label(step)}")


def _latest_step(db: Session, task: Task) -> Step | None:
    return db.scalar(select(Step).where(Step.task_id == task.id)
                     .order_by(Step.seq.desc(), Step.attempt.desc()).limit(1))


def _problem(db: Session, task: Task, step: Step) -> None:
    task.status = "problem"
    label = ERROR_LABELS.get(step.error_class, step.error_class)
    db.add(Event(task_id=task.id, step_id=step.id, level="error",
                 message=f"{step.kind} 단계 {step.attempt}회째 실패: {label}"))


def _model_label(step: Step) -> str:
    """The lane and, when the runner reported it, the concrete model: 'claude/claude-opus-5-5'."""
    name = ((step.output or {}).get("_meta") or {}).get("model")
    return f"{step.model}/{name}" if name else step.model


def _finish_review(db: Session, task: Task, step: Step, now: datetime, usage: dict[str, float | None]) -> bool:
    """Accept the LLM's answers and continue the pipeline; on invalid answers mark the step failed (retried)."""
    request = human.open_request(db, task)
    is_draft = request.answered_by == "draft"
    try:
        answers = human.validate_answers(request.form, step.output["answers"], partial=is_draft)
    except ValueError as exc:
        step.status, step.error_class = "failed", "bad_result"
        db.add(Event(task_id=task.id, step_id=step.id, level="warn", message=f"LLM 판정 형식 오류: {exc}"[:500]))
        return False
    if is_draft:
        human.accept_draft(db, request, answers, step.id, step.model, step.output.get("summary"), now)
        return True
    human.accept_ai_answers(db, request, answers, now, _model_label(step))
    _continue_after_request(db, task, request, step.seq, usage)
    return True


def _continue_after_request(db: Session, task: Task, request: HumanRequest, seq: int,
                            usage: dict[str, float | None]) -> None:
    """The form is answered: move on from the step that asked for it."""
    requester = db.get(Step, request.step_id)
    outputs = _outputs(db, task)
    following = next_kind(requester.kind, requester.output, outputs)
    if following is not None:
        _new_step(db, task, seq + 1, following, 1, run_spec(requester.kind, requester.output, outputs), usage)


def _after_review(db: Session, task: Task, step: Step, now: datetime, usage: dict[str, float | None]) -> bool:
    """A succeeded review is applied once. After an AI draft it stays the latest step, so later ticks either
    start a fresh draft (asked again) or continue the pipeline once the person has submitted."""
    request = human.open_request(db, task)
    if request is not None and request.answered_by in ("llm", "draft"):
        if (request.draft or {}).get("step_id") == step.id:
            _new_step(db, task, step.seq + 1, "review", 1, None, usage)
            return True
        return _finish_review(db, task, step, now, usage)
    submitted = human.last_submitted(db, task)
    if request is None and submitted is not None:
        _continue_after_request(db, task, submitted, step.seq, usage)
    return True


def _review_answered_by_person(db: Session, task: Task, step: Step, usage: dict[str, float | None]) -> bool:
    """The agent failed to answer a form and the person answered it instead: move on, no new review."""
    if step.kind != "review" or step.ended_at is None or human.open_request(db, task) is not None:
        return False
    submitted = human.last_submitted(db, task)
    if submitted is None or submitted.answered_by != "human" or submitted.submitted_at < step.ended_at:
        return False
    _continue_after_request(db, task, submitted, _latest_step(db, task).seq, usage)
    return True


def _stale_form(db: Session, task: Task) -> bool:
    """A running task never waits on a person's form; if one is left pending, stop loudly instead of idling."""
    request = human.open_request(db, task)
    if request is None or request.answered_by != "human":
        return False
    task.status = "problem"
    db.add(Event(task_id=task.id, level="error",
                 message=f"진행 중인데 사람 입력 폼(요청 {request.id}, step {request.step_id})이 "
                         "대기 상태로 남아 멈춤 — 확인 필요"))
    return True


def _rerun_with_new_input(db: Session, task: Task, step: Step) -> bool:
    """A person re-answered a form after this step failed: run the step again with the new answers."""
    latest = db.scalar(select(HumanRequest).where(HumanRequest.task_id == task.id, HumanRequest.status == "submitted")
                       .order_by(HumanRequest.submitted_at.desc()).limit(1))
    if latest is None or step.ended_at is None:
        return False
    files = human.submitted_files(db, task)
    if files == step.input.get("files") and latest.submitted_at <= step.ended_at:
        return False
    db.add(Step(task_id=task.id, seq=step.seq, kind=step.kind, model=step.model, attempt=step.attempt + 1,
                input={**step.input, "files": files}))
    db.add(Event(task_id=task.id, step_id=step.id, message=f"새 입력으로 {step.kind} 단계 다시 실행"))
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


def _latest_pipeline_step(db: Session, task: Task) -> Step | None:
    """A diagnosis is a side note on a stopped task, not a stage the pipeline continues from."""
    return db.scalar(select(Step).where(Step.task_id == task.id, Step.kind != "diagnose")
                     .order_by(Step.seq.desc(), Step.attempt.desc()).limit(1))


def _advance(db: Session, task: Task, now: datetime, usage: dict[str, float | None]) -> None:
    step = _latest_pipeline_step(db, task)
    if step is None or step.status in ("pending", "leased", "running"):
        return
    if _stale_form(db, task):
        return
    if step.status == "succeeded" and step.kind == "approve":
        _apply_agent_approval(db, task, step, now, usage)
        return
    if step.status == "succeeded" and step.kind == "review" and _after_review(db, task, step, now, usage):
        return
    if step.status == "succeeded":
        form = (step.output or {}).get("human_input")
        request = human.request_for_step(db, step)
        if form and request is None:
            project = db.get(Project, task.project_id)
            request = human.create_request(db, task, step, form,
                                           by_llm=human.reviews_by_ai(project, task, form))
        if request is not None and request.status == "pending":
            if request.answered_by in ("llm", "draft"):
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
    if _review_answered_by_person(db, task, step, usage):
        return
    if _rerun_with_new_input(db, task, step):
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
    _auto_start_proposed(db, now)
    _start_approved(db, usage)
    _decide_result_gates(db, now, usage)
    _diagnose_problems(db, now, usage)
    db.flush()
    for task in db.scalars(select(Task).where(Task.status == "running")).all():
        _guarded(db, task, "단계 진행", lambda t=task: _advance(db, t, now, usage))
    db.commit()
