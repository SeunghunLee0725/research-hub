from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.db.models import TASK_FINISHED, Approval, Event, Project, Step, Task
from hub.services import human


def create_task(db: Session, project: Project, title: str, objective: str,
                success_criteria: str | None, parent_task_id: int | None = None) -> Task:
    task = Task(project_id=project.id, title=title, objective=objective, success_criteria=success_criteria,
                status="proposed", parent_task_id=parent_task_id)
    db.add(task)
    db.flush()
    db.add(Approval(task_id=task.id, kind="start", status="pending"))
    db.add(Event(task_id=task.id, message="작업 제안됨 — 시작 승인 대기"))
    db.commit()
    return task


def pending_approval(db: Session, task: Task) -> Approval | None:
    return db.scalar(select(Approval).where(Approval.task_id == task.id, Approval.status == "pending"))


def _decide(db: Session, task: Task, kind: str, status: str, now: datetime, choice: dict | None = None,
            decided_by: str = "human") -> None:
    approval = pending_approval(db, task)
    if approval is None or approval.kind != kind:
        raise ValueError(f"대기 중인 {kind} 승인이 없습니다")
    approval.status, approval.decided_at, approval.choice = status, now, choice
    approval.decided_by = decided_by


def approve_start(db: Session, task: Task, now: datetime, review_by: str | None = None,
                  decided_by: str = "human") -> None:
    if task.status == "proposed" and pending_approval(db, task) is None:
        # The row is what a person clicks; without it a proposed task could never be started.
        db.add(Approval(task_id=task.id, kind="start", status="pending"))
        db.flush()
    _decide(db, task, "start", "approved", now, decided_by=decided_by)
    task.status, task.review_by = "approved", review_by
    who = "시작 승인됨" if decided_by == "human" else f"시작 승인됨 ({decided_by})"
    db.add(Event(task_id=task.id, message=who))
    db.commit()


def open_result_approval(db: Session, task: Task) -> None:
    db.add(Approval(task_id=task.id, kind="result", status="pending"))


def approve_result(db: Session, task: Task, option_index: int | None, now: datetime,
                   review_by: str | None = None, decided_by: str = "human") -> Task | None:
    """Accept the result. With an option index, the chosen next task starts right away (one click)."""
    options = (task.result_card or {}).get("next_options", [])
    if option_index is not None and not 0 <= option_index < len(options):
        raise ValueError("다음 작업 선택이 올바르지 않습니다")
    choice = {"option": option_index, "title": options[option_index]["title"]} if option_index is not None else {"option": None}
    _decide(db, task, "result", "approved", now, choice, decided_by)
    task.status = "done"
    who = "" if decided_by == "human" else f" (에이전트 {decided_by.removeprefix('agent:')})"
    db.add(Event(task_id=task.id, message=f"결과 승인 — 완료{who}"))
    db.flush()
    if option_index is None:
        db.commit()
        return None
    option = options[option_index]
    follow = Task(project_id=task.project_id, parent_task_id=task.id, title=option["title"][:200],
                  objective=f"{option['why']}\n\n(이전 작업 #{task.id} \"{task.title}\"의 결과에서 이어짐)",
                  status="approved", review_by=review_by)
    db.add(follow)
    db.flush()
    db.add(Approval(task_id=follow.id, kind="start", status="approved", decided_at=now,
                    decided_by=decided_by, choice={"via": "result", "from_task": task.id}))
    db.add(Event(task_id=follow.id, message=f"작업 #{task.id} 결과 승인과 함께 시작 승인됨"))
    db.commit()
    return follow


def cancel(db: Session, task: Task, now: datetime) -> None:
    if task.status in ("done", "cancelled"):
        raise ValueError("이미 끝난 작업입니다")
    approval = pending_approval(db, task)
    if approval is not None:
        approval.status, approval.decided_at = "rejected", now
    task.status = "cancelled"
    db.add(Event(task_id=task.id, level="warn", message="사용자가 작업 취소"))
    db.commit()


def retry_problem(db: Session, task: Task, now: datetime) -> None:
    from hub.db.models import Step
    if task.status != "problem":
        raise ValueError("문제 상태의 작업만 재시도할 수 있습니다")
    last = db.scalar(select(Step).where(Step.task_id == task.id).order_by(Step.seq.desc(), Step.attempt.desc()).limit(1))
    if last is None:
        raise ValueError("재시도할 단계가 없습니다")
    db.add(Step(task_id=task.id, seq=last.seq, kind=last.kind, model=last.model, attempt=last.attempt + 1,
                input=last.input))
    task.status = "running"
    db.add(Event(task_id=task.id, message=f"사용자가 {last.kind} 단계 재시도"))
    db.commit()

def _stop_reason(db: Session, task: Task) -> str:
    """Where the previous attempt stopped, in the words the task's own record already holds."""
    from hub.services.scheduler import ERROR_LABELS

    step = db.scalar(select(Step).where(Step.task_id == task.id)
                     .order_by(Step.seq.desc(), Step.attempt.desc()).limit(1))
    if step is None:
        return f"작업 #{task.id} 은 단계를 시작하기 전에 {task.status} 로 끝났다."
    label = ERROR_LABELS.get(step.error_class, step.error_class) if step.error_class else None
    where = f"{step.kind} 단계({step.attempt}회째)"
    if label is None:
        return f"작업 #{task.id} 은 {where}까지 진행한 뒤 {task.status} 로 끝났다."
    lines = [f"작업 #{task.id} 은 {where}에서 멈췄다: {label}."]
    command = (step.input or {}).get("command")
    if command:
        lines.append(f"그때 돌린 명령: {command[:500]}")
    tail = ((step.output or {}).get("log_tail") or "").strip()
    if tail:
        lines.append(f"로그 끝부분: {tail[-500:]}")
    return "\n".join(lines)


def resume_from(db: Session, project: Project, now: datetime, previous: Task | None = None) -> Task:
    """Propose the previous task again: same title, objective and criteria, plus how it ended."""
    if db.scalar(select(Task).where(Task.project_id == project.id,
                                    Task.status.notin_(TASK_FINISHED)).limit(1)) is not None:
        raise ValueError("이 프로젝트에는 이미 진행 중인 작업이 있습니다(프로젝트당 1개).")
    if previous is None:
        previous = db.scalar(select(Task).where(Task.project_id == project.id)
                             .order_by(Task.id.desc()).limit(1))
    if previous is None:
        raise ValueError("이어서 시작할 이전 작업이 없습니다")
    objective = (f"{previous.objective}\n\n## 이어서 하는 이유\n{_stop_reason(db, previous)}\n"
                 f"같은 목표를 다시 시도한다. 이전 작업이 남긴 산출물은 그대로 재사용하고, 멈춘 지점부터 확인한다.")
    task = create_task(db, project, previous.title, objective[:8000], previous.success_criteria,
                       parent_task_id=previous.id)
    db.add(Event(task_id=previous.id, message="직전 작업을 이어서 다시 제안함"))
    db.add(Event(task_id=task.id, message=f"작업 #{previous.id} 의 목표·성공 기준을 이어받았다"))
    db.commit()
    return task

def apply_fix(db: Session, task: Task, now: datetime, decided_by: str = "human") -> None:
    """Restart the failed step with the agent's fix spelled out in its prompt."""
    from hub.db.models import Step

    approval = pending_approval(db, task)
    if approval is None or approval.kind != "fix":
        raise ValueError("대기 중인 수정 제안이 없습니다")
    plan = approval.choice or {}
    _decide(db, task, "fix", "approved", now, plan, decided_by)
    failed = db.scalar(select(Step).where(Step.task_id == task.id, Step.status == "failed")
                       .order_by(Step.seq.desc(), Step.attempt.desc()).limit(1))
    if failed is None:
        raise ValueError("재시도할 단계가 없습니다")
    note = (f"\n\n## 이번에 고칠 것 (진단: {plan.get('diagnosed_by')})\n"
            f"원인: {plan.get('cause')}\n제안: {plan.get('fix')}\n"
            f"이 제안은 연구자가 승인했다. 제안대로 고친 뒤 이 단계를 다시 수행한다.")
    task.status = "running"
    last = db.scalar(select(Step).where(Step.task_id == task.id).order_by(Step.seq.desc()).limit(1))
    request = human.open_request(db, task) if failed.kind == "review" else None
    if request is not None:
        # The agent could not fill this form; another agent pass would fail the same way.
        request.answered_by, request.draft, request.answered_by_model = "human", None, None
        task.status = "waiting_human"
        db.add(Event(task_id=task.id, message="수정 제안 승인 — AI가 채우지 못한 입력 폼을 연구자에게 넘긴다"))
    elif (failed.input or {}).get("prompt"):
        # A later seq than the diagnosis, so the scheduler continues from this step.
        step_input = {**failed.input, "prompt": failed.input["prompt"] + note}
        db.add(Step(task_id=task.id, seq=last.seq + 1, kind=failed.kind, model=failed.model,
                    attempt=1, input=step_input))
        db.add(Event(task_id=task.id, message=f"수정 제안 승인 — {failed.kind} 단계를 다시 실행한다"))
    else:
        # A run step is a shell command: the fix is in the code, so an implement step makes it first.
        from hub.services.scheduler import _new_step, quota_usage

        step = _new_step(db, task, last.seq + 1, "implement", 1, None, quota_usage(db, now))
        step.input = {**step.input, "prompt": step.input["prompt"] + note}
        db.add(Event(task_id=task.id,
                     message="수정 제안 승인 — 구현 단계에서 고친 뒤 실행을 다시 시도한다"))
    db.commit()


def decline_fix(db: Session, task: Task, now: datetime, decided_by: str = "human") -> None:
    approval = pending_approval(db, task)
    if approval is None or approval.kind != "fix":
        raise ValueError("대기 중인 수정 제안이 없습니다")
    _decide(db, task, "fix", "rejected", now, approval.choice, decided_by)
    db.add(Event(task_id=task.id, message="수정 제안을 적용하지 않기로 했다 — 작업은 멈춘 그대로다"))
    db.commit()
