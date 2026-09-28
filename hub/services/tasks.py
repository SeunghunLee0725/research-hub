from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.db.models import Approval, Event, Project, Task


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


def _decide(db: Session, task: Task, kind: str, status: str, now: datetime, choice: dict | None = None) -> None:
    approval = pending_approval(db, task)
    if approval is None or approval.kind != kind:
        raise ValueError(f"대기 중인 {kind} 승인이 없습니다")
    approval.status, approval.decided_at, approval.choice = status, now, choice


def approve_start(db: Session, task: Task, now: datetime, review_by: str | None = None) -> None:
    _decide(db, task, "start", "approved", now)
    task.status, task.review_by = "approved", review_by
    db.add(Event(task_id=task.id, message="시작 승인됨"))
    db.commit()


def open_result_approval(db: Session, task: Task) -> None:
    db.add(Approval(task_id=task.id, kind="result", status="pending"))


def approve_result(db: Session, task: Task, option_index: int | None, now: datetime,
                   review_by: str | None = None) -> Task | None:
    """Accept the result. With an option index, the chosen next task starts right away (one click)."""
    options = (task.result_card or {}).get("next_options", [])
    if option_index is not None and not 0 <= option_index < len(options):
        raise ValueError("다음 작업 선택이 올바르지 않습니다")
    choice = {"option": option_index, "title": options[option_index]["title"]} if option_index is not None else {"option": None}
    _decide(db, task, "result", "approved", now, choice)
    task.status = "done"
    db.add(Event(task_id=task.id, message="결과 승인 — 완료"))
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
                    choice={"via": "result", "from_task": task.id}))
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
