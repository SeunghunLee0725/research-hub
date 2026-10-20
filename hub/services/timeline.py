"""Gantt data: projects → tasks → step segments, and agent lanes (machine × Claude/Codex/run)."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from hub.db.models import PROJECT_KINDS, TASK_FINISHED, Node, Project, Step, Task
from hub.services.gantt_axis import pct_between

RANGES = {"1": 1, "7": 7, "30": 30}
DEFAULT_RANGE = "7"
ALL_CAP_DAYS = 365  # one task with a wrong date must not stretch the axis over years
AGENTS = ("claude", "codex", "none")
PALETTE_SIZE = 8
LIVE = ("leased", "running")


@dataclass(frozen=True)
class Segment:
    step_id: int
    kind: str
    agent: str
    status: str
    attempt: int
    node: str | None
    start: datetime
    end: datetime
    left: float
    width: float
    model_name: str | None
    error_class: str | None


@dataclass(frozen=True)
class TaskBar:
    task_id: int
    title: str
    status: str
    node: str | None
    start: datetime
    end: datetime
    left: float
    width: float
    segments: tuple[Segment, ...]


@dataclass(frozen=True)
class ProjectRow:
    slug: str
    name: str
    kind: str
    node: str | None
    bars: tuple[TaskBar, ...]
    period: tuple[float, float] | None
    deadline_pct: float | None
    starts_on: date | None
    ends_on: date | None


@dataclass(frozen=True)
class Group:
    kind: str
    rows: tuple[ProjectRow, ...]


@dataclass(frozen=True)
class Gantt:
    start: datetime
    end: datetime
    groups: tuple[Group, ...]


@dataclass(frozen=True)
class LaneSegment:
    project: str
    task_id: int
    title: str
    kind: str
    status: str
    start: datetime
    end: datetime
    left: float
    width: float
    color: int
    model_name: str | None


@dataclass(frozen=True)
class Lane:
    node: str
    agent: str
    tracks: tuple[tuple[LaneSegment, ...], ...]

    @property
    def key(self) -> tuple[str, str]:
        return self.node, self.agent


@dataclass(frozen=True)
class AgentLanes:
    start: datetime
    end: datetime
    lanes: tuple[Lane, ...]
    legend: tuple[tuple[str, int], ...]


def window(key: str, now: datetime, earliest: datetime | None) -> tuple[datetime, datetime]:
    if key == "all":
        days = (now - earliest).days + 1 if earliest else RANGES[DEFAULT_RANGE]
        days = max(1, min(ALL_CAP_DAYS, days))
    else:
        days = RANGES.get(key, RANGES[DEFAULT_RANGE])
    span = timedelta(days=days)
    return now - span, now + span * 0.04


def earliest_task(db: Session) -> datetime | None:
    return db.scalar(select(Task.created_at).order_by(Task.created_at).limit(1))


def model_name(step: Step) -> str | None:
    meta = (step.output or {}).get("_meta") if isinstance(step.output, dict) else None
    return meta.get("model") if isinstance(meta, dict) else None


def step_end(step: Step, now: datetime) -> datetime:
    if step.ended_at:
        return step.ended_at
    return now if step.status in LIVE else (step.last_progress_at or step.started_at)


def _projects(db: Session, kind: str | None) -> list[Project]:
    query = select(Project).order_by(Project.name)
    return list(db.scalars(query.where(Project.kind == kind) if kind else query))


def _tasks_in_window(db: Session, project_ids: list[int], start: datetime, end: datetime) -> list[Task]:
    if not project_ids:
        return []
    return list(db.scalars(select(Task).where(
        Task.project_id.in_(project_ids), Task.created_at <= end,
        or_(Task.status.not_in(TASK_FINISHED), Task.updated_at >= start)).order_by(Task.created_at)))


def _started_steps(db: Session, task_ids: list[int]) -> list[Step]:
    if not task_ids:
        return []
    return list(db.scalars(select(Step).where(Step.task_id.in_(task_ids), Step.started_at.is_not(None))
                           .order_by(Step.started_at, Step.id)))


def _node_names(db: Session) -> dict[int, str]:
    return dict(db.execute(select(Node.id, Node.name)).all())


def _overlaps(a: datetime, b: datetime, start: datetime, end: datetime) -> bool:
    return b >= start and a <= end


def _span(start: datetime, end: datetime, a: datetime, b: datetime) -> tuple[float, float]:
    left = pct_between(start, end, a)
    return left, pct_between(start, end, b) - left


def _segment(step: Step, now: datetime, start: datetime, end: datetime, nodes: dict[int, str]) -> Segment | None:
    b = step_end(step, now)
    if not _overlaps(step.started_at, b, start, end):
        return None
    left, width = _span(start, end, step.started_at, b)
    return Segment(step.id, step.kind, step.model, step.status, step.attempt, nodes.get(step.node_id),
                   step.started_at, b, left, width, model_name(step), step.error_class)


def _bar(task: Task, steps: list[Step], now: datetime, start: datetime, end: datetime,
         nodes: dict[int, str]) -> TaskBar | None:
    if task.status in TASK_FINISHED:
        b = max((step_end(s, now) for s in steps), default=task.updated_at)
    else:
        b = now
    if not _overlaps(task.created_at, b, start, end):
        return None
    segments = tuple(seg for s in steps if (seg := _segment(s, now, start, end, nodes)))
    left, width = _span(start, end, task.created_at, b)
    return TaskBar(task.id, task.title, task.status, nodes.get(task.node_id), task.created_at, b, left, width,
                   segments)


def _day_start(day: date, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def _period(p: Project, start: datetime, end: datetime, tz: ZoneInfo) -> tuple[float, float] | None:
    if not (p.starts_on and p.ends_on):
        return None
    a, b = _day_start(p.starts_on, tz), _day_start(p.ends_on + timedelta(days=1), tz)
    return _span(start, end, a, b) if _overlaps(a, b, start, end) else None


def _deadline(p: Project, start: datetime, end: datetime, tz: ZoneInfo) -> float | None:
    if not p.ends_on:
        return None
    t = _day_start(p.ends_on + timedelta(days=1), tz)
    return pct_between(start, end, t) if start <= t <= end else None


def project_node(p: Project) -> str | None:
    return next((s.removeprefix("node:") for s in p.node_selector or () if s.startswith("node:")), None)


def build_gantt(db: Session, now: datetime, start: datetime, end: datetime, kind: str | None,
                tz_name: str = "Asia/Seoul") -> Gantt:
    tz = ZoneInfo(tz_name)
    projects = _projects(db, kind)
    tasks = _tasks_in_window(db, [p.id for p in projects], start, end)
    steps = _started_steps(db, [t.id for t in tasks])
    nodes = _node_names(db)
    steps_by_task: dict[int, list[Step]] = {}
    for s in steps:
        steps_by_task.setdefault(s.task_id, []).append(s)
    bars_by_project: dict[int, list[TaskBar]] = {}
    for t in tasks:
        if bar := _bar(t, steps_by_task.get(t.id, []), now, start, end, nodes):
            bars_by_project.setdefault(t.project_id, []).append(bar)
    rows = [ProjectRow(p.slug, p.name, p.kind, project_node(p), tuple(bars_by_project.get(p.id, ())),
                       _period(p, start, end, tz), _deadline(p, start, end, tz), p.starts_on, p.ends_on)
            for p in projects]
    groups = tuple(Group(k, tuple(r for r in rows if r.kind == k)) for k in PROJECT_KINDS)
    return Gantt(start, end, tuple(g for g in groups if g.rows))


def pack(segments: list[LaneSegment]) -> tuple[tuple[LaneSegment, ...], ...]:
    """Greedy interval packing: each segment goes to the first track that is free when it starts."""
    tracks: list[list[LaneSegment]] = []
    for seg in sorted(segments, key=lambda s: s.start):
        track = next((t for t in tracks if t[-1].end <= seg.start), None)
        if track is None:
            tracks.append([seg])
        else:
            track.append(seg)
    return tuple(tuple(t) for t in tracks) or ((),)


def build_agent_lanes(db: Session, now: datetime, start: datetime, end: datetime, kind: str | None) -> AgentLanes:
    projects = _projects(db, kind)
    color = {p.id: i % PALETTE_SIZE for i, p in enumerate(projects)}
    by_id = {p.id: p for p in projects}
    tasks = {t.id: t for t in _tasks_in_window(db, list(by_id), start, end)}
    nodes = db.scalars(select(Node).where(Node.disabled.is_(False)).order_by(Node.name)).all()
    names = {n.id: n.name for n in nodes}
    buckets: dict[tuple[str, str], list[LaneSegment]] = {(n.name, a): [] for n in nodes for a in AGENTS}
    for s in _started_steps(db, list(tasks)):
        b = step_end(s, now)
        key = (names.get(s.node_id), s.model)
        if key not in buckets or not _overlaps(s.started_at, b, start, end):
            continue
        task = tasks[s.task_id]
        project = by_id[task.project_id]
        left, width = _span(start, end, s.started_at, b)
        buckets[key].append(LaneSegment(project.name, task.id, task.title, s.kind, s.status, s.started_at, b,
                                        left, width, color[project.id], model_name(s)))
    lanes = tuple(Lane(node, agent, pack(segs)) for (node, agent), segs in buckets.items())
    used = {seg.project for lane in lanes for track in lane.tracks for seg in track}
    legend = tuple((p.name, color[p.id]) for p in projects if p.name in used)
    return AgentLanes(start, end, lanes, legend)
