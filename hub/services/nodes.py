from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.db.models import Node, QuotaSnapshot
from hub.security import generate_token, hash_token


def create_node(db: Session, name: str, labels: list[str]) -> tuple[Node, str]:
    token = generate_token()
    node = Node(name=name, labels=list(labels), token_hash=hash_token(token))
    db.add(node)
    db.commit()
    return node, token


def rotate_token(db: Session, node: Node) -> str:
    token = generate_token()
    node.token_hash = hash_token(token)
    db.commit()
    return token


def find_node_by_token(db: Session, token: str) -> Node | None:
    return db.scalar(select(Node).where(Node.token_hash == hash_token(token)))


def record_heartbeat(db: Session, node: Node, agent_version: str, hostname: str,
                     metrics: dict, now: datetime) -> None:
    node.agent_version = agent_version
    node.hostname = hostname
    node.metrics = metrics
    node.last_seen_at = now
    db.commit()


def record_logins(db: Session, node: Node, logins: dict, now: datetime) -> None:
    node.logins = {**logins, "checked_at": now.isoformat()}
    node.last_seen_at = now
    db.commit()


def record_quota(db: Session, node: Node, provider: str, windows: list[dict],
                 error: str | None, now: datetime) -> None:
    db.add(QuotaSnapshot(node_id=node.id, provider=provider, windows=windows, error=error, at=now))
    db.commit()
