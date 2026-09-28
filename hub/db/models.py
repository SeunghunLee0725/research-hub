from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, LargeBinary, CheckConstraint, DateTime, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TASK_STATUSES = ("proposed", "approved", "running", "waiting_human", "review", "problem", "done", "cancelled")
TASK_FINISHED = ("done", "cancelled")
STEP_KINDS = ("plan", "implement", "run", "review", "analyze", "verify", "report")
STEP_STATUSES = ("pending", "leased", "running", "succeeded", "failed", "lost")
MODELS = ("claude", "codex", "none")
PROVIDERS = ("claude", "codex")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    pass


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    labels: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    agent_version: Mapped[str | None] = mapped_column(String(32))
    hostname: Mapped[str | None] = mapped_column(String(128))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    logins: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (CheckConstraint("max_parallel >= 1", name="max_parallel_positive"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    workdir: Mapped[str] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text)
    node_selector: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'"))
    max_parallel: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    # When a step asks for human judgment, let an LLM fill the form instead of waiting for a person.
    auto_ai_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(_in("status", TASK_STATUSES), name="task_status_valid"),
        CheckConstraint("review_by IS NULL OR review_by IN ('human', 'llm')", name="task_review_by_valid"),
        # One unfinished task per project: the core "one job at a time" rule, enforced by the DB.
        Index("one_active_task_per_project", "project_id", unique=True,
              postgresql_where=text(f"NOT ({_in('status', TASK_FINISHED)})")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    parent_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    title: Mapped[str] = mapped_column(String(200))
    objective: Mapped[str] = mapped_column(Text)
    plan: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    success_criteria: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    node_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id"))
    # Who fills human-review forms for this task: None follows the project setting, else "human" or "llm".
    review_by: Mapped[str | None] = mapped_column(String(8))
    result_card: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class Step(Base):
    __tablename__ = "steps"
    __table_args__ = (
        CheckConstraint(_in("kind", STEP_KINDS), name="step_kind_valid"),
        CheckConstraint(_in("status", STEP_STATUSES), name="step_status_valid"),
        CheckConstraint(_in("model", MODELS), name="step_model_valid"),
        UniqueConstraint("task_id", "seq", "attempt"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    node_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id"))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    output: Mapped[dict | None] = mapped_column(JSONB)
    error_class: Mapped[str | None] = mapped_column(String(32))


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (CheckConstraint(_in("level", ("info", "warn", "error")), name="event_level_valid"),
                      Index("events_task_at", "task_id", "at"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    step_id: Mapped[int | None] = mapped_column(ForeignKey("steps.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    level: Mapped[str] = mapped_column(String(8), default="info", server_default="info")
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("steps.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str | None] = mapped_column(String(32))


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(_in("kind", ("start", "result")), name="approval_kind_valid"),
        CheckConstraint(_in("status", ("pending", "approved", "rejected")), name="approval_status_valid"),
        Index("one_pending_approval", "task_id", "kind", unique=True,
              postgresql_where=text("status = 'pending'")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(8), default="pending", server_default="pending")
    choice: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(200), unique=True)
    event: Mapped[str] = mapped_column(String(32))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)


class QuotaSnapshot(Base):
    __tablename__ = "quota_snapshots"
    __table_args__ = (CheckConstraint(_in("provider", PROVIDERS), name="quota_provider_valid"),
                      Index("quota_provider_at", "provider", "at"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(16))
    windows: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'"))
    error: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HumanRequest(Base):
    """Work only a person can do (labels, judgments). The form lives here; answers go back to the node as a file."""
    __tablename__ = "human_requests"
    __table_args__ = (CheckConstraint(_in("status", ("pending", "submitted")), name="human_request_status_valid"),
                      CheckConstraint(_in("answered_by", ("human", "llm")), name="human_request_answered_by_valid"),
                      UniqueConstraint("step_id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    step_id: Mapped[int] = mapped_column(ForeignKey("steps.id", ondelete="CASCADE"))
    form: Mapped[dict] = mapped_column(JSONB)
    answers_path: Mapped[str] = mapped_column(Text)
    answers: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    answered_by: Mapped[str] = mapped_column(String(8), default="human", server_default="human")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Media(Base):
    """Small files (e.g. X-ray thumbnails) a node uploads so a person can see them in a review form."""
    __tablename__ = "media"
    __table_args__ = (UniqueConstraint("step_id", "path"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("steps.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
