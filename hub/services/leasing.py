from datetime import datetime, timedelta

from sqlalchemy import cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from hub.db.models import Event, Node, Project, Step, Task
from hub.services.pipeline import validate_result

LEASE = timedelta(minutes=10)


class NotYourStep(Exception):
    pass


def node_labels(node: Node) -> list[str]:
    return [*node.labels, f"node:{node.name}"]


def lease_step(db: Session, node: Node, now: datetime) -> Step | None:
    labels = node_labels(node)
    models = [m for m in ("claude", "codex") if m in labels] + ["none"]
    step = db.scalar(
        select(Step).join(Task, Task.id == Step.task_id).join(Project, Project.id == Task.project_id)
        .where(Step.status == "pending", or_(Step.not_before.is_(None), Step.not_before <= now),
               Task.status == "running", or_(Task.node_id.is_(None), Task.node_id == node.id),
               Project.node_selector.op("<@")(cast(labels, JSONB)), Step.model.in_(models))
        .order_by(Step.id).limit(1).with_for_update(of=Step, skip_locked=True))
    if step is None:
        return None
    task = db.get(Task, step.task_id)
    task.node_id = task.node_id or node.id
    step.status, step.node_id, step.lease_until, step.started_at = "leased", node.id, now + LEASE, now
    db.add(Event(task_id=task.id, step_id=step.id, message=f"{step.kind} 단계 시작 ({node.name})"))
    db.commit()
    return step


def assert_owned(step: Step, node: Node) -> None:
    if step.node_id != node.id or step.status not in ("leased", "running"):
        raise NotYourStep(f"step {step.id} is not leased by {node.name}")


def record_progress(db: Session, step: Step, node: Node, message: str | None, now: datetime) -> None:
    assert_owned(step, node)
    step.status, step.lease_until, step.last_progress_at = "running", now + LEASE, now
    if message:
        db.add(Event(task_id=step.task_id, step_id=step.id, message=message[:500]))
    db.commit()


def complete_step(db: Session, step: Step, node: Node, status: str, result: dict | None,
                  error_class: str | None, now: datetime) -> None:
    assert_owned(step, node)
    step.ended_at, step.lease_until = now, None
    if status == "succeeded":
        try:
            step.output = validate_result(step.kind, result)
            step.status, step.error_class = "succeeded", None
        except ValueError as exc:
            step.status, step.error_class, step.output = "failed", "bad_result", {"raw": result, "error": str(exc)}
    else:
        step.status, step.error_class, step.output = "failed", error_class or "tool_error", result
    level = "info" if step.status == "succeeded" else "warn"
    note = "완료" if step.status == "succeeded" else f"실패 ({step.error_class})"
    db.add(Event(task_id=step.task_id, step_id=step.id, level=level, message=f"{step.kind} 단계 {note}"))
    db.commit()


def step_payload(step: Step, project: Project) -> dict:
    return {"id": step.id, "task_id": step.task_id, "kind": step.kind, "model": step.model,
            "attempt": step.attempt, "workdir": project.workdir, **step.input}
