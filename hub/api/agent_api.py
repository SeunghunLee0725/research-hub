from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from hub.api.agent_schemas import Heartbeat, Logins, Quota
from hub.api.envelope import ok
from hub.db.models import Node
from hub.db.session import get_db
from hub.services import nodes

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
