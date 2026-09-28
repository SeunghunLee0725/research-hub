from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from hub.db.models import TASK_FINISHED, Event, Node, Project, QuotaSnapshot, Task

QUOTA_LOOKBACK = timedelta(hours=24)


@dataclass(frozen=True)
class NodeView:
    name: str
    state: str
    last_seen_at: datetime | None
    labels: tuple[str, ...]
    gpu_util: list
    mem_used_mb: int | None
    mem_total_mb: int | None
    disk_used_gb: float | None
    disk_total_gb: float | None
    claude: str
    codex: str
    claude_plan: str | None
    codex_plan: str | None


@dataclass(frozen=True)
class QuotaView:
    provider: str
    windows: list
    error: str | None
    at: datetime


@dataclass(frozen=True)
class ProjectView:
    slug: str
    name: str
    status: str
    task_title: str | None
    node: str | None
    updated_at: datetime | None
    task_id: int | None = None
    last_event: str | None = None
    last_event_at: datetime | None = None


@dataclass(frozen=True)
class Overview:
    nodes: tuple[NodeView, ...]
    quotas: tuple[QuotaView, ...]
    projects: tuple[ProjectView, ...]
    generated_at: datetime


def node_state(last_seen: datetime | None, now: datetime, offline_after_seconds: int) -> str:
    if last_seen is None:
        return "never"
    return "online" if (now - last_seen).total_seconds() <= offline_after_seconds else "offline"


def login_badge(login: dict | None) -> str:
    if not login or login.get("logged_in") is None:
        return "unknown"
    return "ok" if login["logged_in"] else "logged_out"


def _node_view(node: Node, now: datetime, offline_after: int) -> NodeView:
    m, logins = node.metrics or {}, node.logins or {}
    return NodeView(
        name=node.name, state=node_state(node.last_seen_at, now, offline_after),
        last_seen_at=node.last_seen_at, labels=tuple(node.labels or ()),
        gpu_util=[g.get("util_pct") for g in m.get("gpus", [])],
        mem_used_mb=m.get("mem_used_mb"), mem_total_mb=m.get("mem_total_mb"),
        disk_used_gb=m.get("disk_used_gb"), disk_total_gb=m.get("disk_total_gb"),
        claude=login_badge(logins.get("claude")), codex=login_badge(logins.get("codex")),
        claude_plan=(logins.get("claude") or {}).get("subscription"),
        codex_plan=(logins.get("codex") or {}).get("subscription"),
    )


def _latest_quotas(db: Session, now: datetime) -> tuple[QuotaView, ...]:
    # Subscriptions are per account, not per machine: the freshest snapshot from any node wins.
    rows = db.scalars(select(QuotaSnapshot).ext(distinct_on(QuotaSnapshot.provider)).where(QuotaSnapshot.at >= now - QUOTA_LOOKBACK)
                      .order_by(QuotaSnapshot.provider, QuotaSnapshot.at.desc())).all()
    return tuple(QuotaView(r.provider, r.windows, r.error, r.at) for r in rows)


def _project_views(db: Session) -> tuple[ProjectView, ...]:
    active = (select(Task.id, Task.project_id, Task.title, Task.status, Task.updated_at, Node.name.label("node"))
              .outerjoin(Node, Node.id == Task.node_id)
              .where(Task.status.not_in(TASK_FINISHED)).subquery())
    rows = db.execute(select(Project.slug, Project.name, active.c.id, active.c.title, active.c.status,
                             active.c.node, active.c.updated_at)
                      .outerjoin(active, active.c.project_id == Project.id)
                      .order_by(Project.name)).all()
    return tuple(_project_view(db, r) for r in rows)


def _project_view(db: Session, r) -> ProjectView:
    event = db.scalar(select(Event).where(Event.task_id == r.id).order_by(Event.id.desc()).limit(1)) if r.id else None
    return ProjectView(slug=r.slug, name=r.name, status=r.status or "idle", task_title=r.title, node=r.node,
                       updated_at=r.updated_at, task_id=r.id, last_event=event.message if event else None,
                       last_event_at=event.at if event else None)


def build_overview(db: Session, now: datetime, offline_after_seconds: int) -> Overview:
    nodes = db.scalars(select(Node).where(Node.disabled.is_(False)).order_by(Node.name)).all()
    return Overview(
        nodes=tuple(_node_view(n, now, offline_after_seconds) for n in nodes),
        quotas=_latest_quotas(db, now),
        projects=_project_views(db),
        generated_at=now,
    )
