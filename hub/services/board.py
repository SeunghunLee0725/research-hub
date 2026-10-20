"""Status board: every task in a column by where it is now, with the agent working on it."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from hub.db.models import TASK_FINISHED, Event, Node, Project, Step, Task
from hub.services.timeline import model_name

COLUMN_ORDER = ("proposed", "approved", "running", "waiting_human", "review", "problem", "done", "cancelled")
CONCLUSION_CHARS = 140


@dataclass(frozen=True)
class Chip:
    kind: str
    agent: str
    status: str
    model_name: str | None = None


@dataclass(frozen=True)
class Card:
    task_id: int
    title: str
    status: str
    project: str
    project_slug: str
    kind: str
    node: str | None
    current: Chip | None
    chain: tuple[Chip, ...]
    retries: int
    started_at: datetime | None
    updated_at: datetime
    last_event: str | None
    last_event_at: datetime | None
    conclusion: str | None


@dataclass(frozen=True)
class Column:
    status: str
    cards: tuple[Card, ...]


@dataclass(frozen=True)
class Board:
    columns: tuple[Column, ...]
    counts: dict = field(default_factory=dict)

    def column(self, status: str) -> Column:
        return next(c for c in self.columns if c.status == status)


def _tasks(db: Session, now: datetime, kind: str | None, done_days: int) -> list[tuple[Task, Project]]:
    query = (select(Task, Project).join(Project, Project.id == Task.project_id)
             .where(or_(Task.status.not_in(TASK_FINISHED), Task.updated_at >= now - timedelta(days=done_days)))
             .order_by(Task.updated_at.desc()))
    if kind:
        query = query.where(Project.kind == kind)
    return [(t, p) for t, p in db.execute(query).all()]


def _steps(db: Session, task_ids: list[int]) -> dict[int, list[Step]]:
    out: dict[int, list[Step]] = {}
    if task_ids:
        for s in db.scalars(select(Step).where(Step.task_id.in_(task_ids)).order_by(Step.seq, Step.attempt)):
            out.setdefault(s.task_id, []).append(s)
    return out


def _last_events(db: Session, task_ids: list[int]) -> dict[int, Event]:
    if not task_ids:
        return {}
    rows = db.scalars(select(Event).ext(distinct_on(Event.task_id)).where(Event.task_id.in_(task_ids))
                      .order_by(Event.task_id, Event.id.desc()))
    return {e.task_id: e for e in rows}


def _chain(steps: list[Step]) -> tuple[Chip, ...]:
    latest: dict[int, Step] = {}
    for s in steps:  # ordered by seq, attempt: the last attempt of each seq wins
        latest[s.seq] = s
    return tuple(Chip(s.kind, s.model, s.status, model_name(s)) for _, s in sorted(latest.items()))


def _conclusion(task: Task) -> str | None:
    text = (task.result_card or {}).get("conclusion") if isinstance(task.result_card, dict) else None
    return text[:CONCLUSION_CHARS] if isinstance(text, str) and text else None


def _card(task: Task, project: Project, steps: list[Step], event: Event | None, nodes: dict[int, str]) -> Card:
    chain = _chain(steps)
    started = min((s.started_at for s in steps if s.started_at), default=None)
    return Card(task.id, task.title, task.status, project.name, project.slug, project.kind,
                nodes.get(task.node_id), chain[-1] if chain else None, chain, len(steps) - len(chain), started,
                task.updated_at, event.message if event else None, event.at if event else None,
                _conclusion(task))


def build_board(db: Session, now: datetime, kind: str | None, done_days: int) -> Board:
    rows = _tasks(db, now, kind, done_days)
    ids = [t.id for t, _ in rows]
    steps, events = _steps(db, ids), _last_events(db, ids)
    nodes = dict(db.execute(select(Node.id, Node.name)).all())
    cards = [_card(t, p, steps.get(t.id, []), events.get(t.id), nodes) for t, p in rows]
    columns = tuple(Column(status, tuple(c for c in cards if c.status == status)) for status in COLUMN_ORDER)
    return Board(columns, {c.status: len(c.cards) for c in columns})
