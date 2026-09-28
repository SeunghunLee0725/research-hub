from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import base64
import binascii
import hashlib

from hub.api.agent_schemas import Complete, Heartbeat, Logins, MediaUpload, Progress, Quota
from hub.api.envelope import ok
from hub.db.models import Media, Node, Project, Step, Task
from hub.db.session import get_db
from hub.services import leasing, nodes

router = APIRouter(prefix="/agent/v1")
Db = Annotated[Session, Depends(get_db)]


def current_node(db: Db, authorization: Annotated[str | None, Header()] = None) -> Node:
    scheme, _, token = (authorization or "").partition(" ")
    node = nodes.find_node_by_token(db, token) if scheme == "Bearer" and token else None
    if node is None:
        raise HTTPException(401, "invalid node token")
    if node.disabled:
        raise HTTPException(403, "node disabled")
    return node


AgentNode = Annotated[Node, Depends(current_node)]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/heartbeat")
def heartbeat(body: Heartbeat, node: AgentNode, db: Db) -> dict:
    nodes.record_heartbeat(db, node, body.agent_version, body.hostname, body.metrics.model_dump(), _now())
    return ok({"node": node.name})


@router.post("/logins")
def logins(body: Logins, node: AgentNode, db: Db) -> dict:
    nodes.record_logins(db, node, body.model_dump(), _now())
    return ok({"node": node.name})


@router.post("/quota")
def quota(body: Quota, node: AgentNode, db: Db) -> dict:
    nodes.record_quota(db, node, body.provider, [w.model_dump() for w in body.windows], body.error, _now())
    return ok({"node": node.name})


@router.post("/lease")
def lease(node: AgentNode, db: Db) -> dict:
    step = leasing.lease_step(db, node, _now())
    if step is None:
        return ok({"step": None})
    project = db.get(Project, db.get(Task, step.task_id).project_id)
    return ok({"step": leasing.step_payload(step, project)})


def _step(db: Session, step_id: int) -> Step:
    step = db.get(Step, step_id)
    if step is None:
        raise HTTPException(404, "unknown step")
    return step


@router.post("/steps/{step_id}/progress")
def progress(step_id: int, body: Progress, node: AgentNode, db: Db) -> dict:
    try:
        leasing.record_progress(db, _step(db, step_id), node, body.message, _now())
    except leasing.NotYourStep as exc:
        raise HTTPException(409, str(exc)) from exc
    return ok({"step": step_id})


@router.post("/steps/{step_id}/complete")
def complete(step_id: int, body: Complete, node: AgentNode, db: Db) -> dict:
    try:
        leasing.complete_step(db, _step(db, step_id), node, body.status, body.result, body.error_class, _now())
    except leasing.NotYourStep as exc:
        raise HTTPException(409, str(exc)) from exc
    return ok({"step": step_id})


MAX_MEDIA_BYTES = 5 * 1024 * 1024


@router.post("/steps/{step_id}/media")
def upload_media(step_id: int, body: MediaUpload, node: AgentNode, db: Db) -> dict:
    step = _step(db, step_id)
    try:
        leasing.assert_owned(step, node)
    except leasing.NotYourStep as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        data = base64.b64decode(body.data_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "invalid base64") from exc
    if len(data) > MAX_MEDIA_BYTES:
        raise HTTPException(413, "file too large")
    existing = db.scalar(select(Media).where(Media.step_id == step_id, Media.path == body.path))
    if existing is None:
        db.add(Media(step_id=step_id, path=body.path, content_type=body.content_type,
                     sha256=hashlib.sha256(data).hexdigest(), data=data))
        db.commit()
    return ok({"path": body.path})
