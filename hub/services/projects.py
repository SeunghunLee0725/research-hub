from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.db.models import TASK_FINISHED, Project, Task


def create_project(db: Session, slug: str, name: str, workdir: str, node_selector: list[str],
                   kind: str = "research") -> Project:
    project = Project(slug=slug, name=name, workdir=workdir, node_selector=list(node_selector), kind=kind)
    db.add(project)
    db.commit()
    return project


def ensure_open(project: Project) -> None:
    if project.closed_at is not None:
        raise ValueError("종료된 프로젝트입니다 — 다시 열어야 작업·자료를 추가할 수 있습니다")


def close_project(db: Session, project: Project, now: datetime, note: str | None) -> Task | None:
    """End a finished project: cancel its open task so nothing follows. Records and the workdir are kept."""
    from hub.services import tasks

    ensure_open(project)
    active = db.scalar(select(Task).where(Task.project_id == project.id, Task.status.notin_(TASK_FINISHED)))
    if active is not None:
        tasks.cancel(db, active, now, "프로젝트 종료로 작업 취소 — 기록과 산출물은 그대로 남는다")
    project.closed_at, project.closed_note = now, note
    db.commit()
    return active


def reopen_project(db: Session, project: Project) -> None:
    if project.closed_at is None:
        raise ValueError("종료된 프로젝트가 아닙니다")
    project.closed_at, project.closed_note = None, None
    db.commit()
