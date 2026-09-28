from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.db.models import Event, HumanRequest, Node, Project, Step, Task
from hub.services import human

STEP_LABELS = {"plan": "계획", "implement": "구현", "run": "실행", "analyze": "분석", "verify": "검증", "report": "보고"}


@dataclass(frozen=True)
class TaskDetail:
    task: Task
    project: Project
    node: str | None
    steps: tuple[Step, ...]
    events: tuple[Event, ...]
    request: HumanRequest | None = None


def load(db: Session, task_id: int, event_limit: int = 100) -> TaskDetail | None:
    task = db.get(Task, task_id)
    if task is None:
        return None
    node = db.get(Node, task.node_id) if task.node_id else None
    steps = db.scalars(select(Step).where(Step.task_id == task_id).order_by(Step.seq, Step.attempt)).all()
    events = db.scalars(select(Event).where(Event.task_id == task_id)
                        .order_by(Event.id.desc()).limit(event_limit)).all()
    return TaskDetail(task, db.get(Project, task.project_id), node.name if node else None,
                      tuple(steps), tuple(events), human.open_request(db, task))
