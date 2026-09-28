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


def approve_start(db: Session, task: Task, now: datetime) -> None:
    _decide(db, task, "start", "approved", now)
    task.status = "approved"
    db.add(Event(task_id=task.id, message="시작 승인됨"))
    db.commit()


def open_result_approval(db: Session, task: Task) -> None:
    db.add(Approval(task_id=task.id, kind="result", status="pending"))
