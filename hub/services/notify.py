"""Telegram alerts: start approval needed, problem, result arrived, node offline / logged out.

Task events are sent once per key. Node conditions are re-armed when they clear."""
import json
import logging
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hub.db.models import Approval, Event, Node, Notification, Project, Task
from hub.services.overview import login_badge, node_state

log = logging.getLogger(__name__)
Sender = Callable[[str], None]
CONDITION_PREFIX = "node:"
PROVIDER_NAMES = {"claude": "Claude", "codex": "Codex"}


@dataclass(frozen=True)
class Alert:
    key: str
    event: str
    text: str
    task_id: int | None = None


def _task_alerts(db: Session, base_url: str) -> list[Alert]:
    rows = db.execute(select(Task, Project.name).join(Project, Project.id == Task.project_id)
                      .where(Task.status.in_(("proposed", "review", "problem", "waiting_human")))).all()
    alerts = []
    for task, project in rows:
        link = f"{base_url}/tasks/{task.id}"
        if task.status == "proposed":
            alerts.append(Alert(f"task:{task.id}:start", "start",
                                f"🟡 승인 필요 · {project}\n\"{task.title}\" 시작 승인을 기다립니다.\n{link}", task.id))
        elif task.status == "waiting_human":
            alerts.append(Alert(f"task:{task.id}:human:{task.updated_at.isoformat() if task.updated_at else ''}",
                                "human", f"🟡 사람 작업 필요 · {project}\n\"{task.title}\"\n웹에서 입력해 주세요: {link}",
                                task.id))
        elif task.status == "review":
            conclusion = (task.result_card or {}).get("conclusion", "")
            alerts.append(Alert(f"task:{task.id}:review", "review",
                                f"🟢 결과 도착 · {project}\n\"{task.title}\"\n{conclusion}\n결과 승인: {link}", task.id))
        else:
            fix = db.scalar(select(Approval).where(Approval.task_id == task.id, Approval.kind == "fix",
                                                   Approval.status == "pending"))
            if fix is not None:
                choice = fix.choice or {}
                alerts.append(Alert(f"task:{task.id}:fix:{fix.id}", "fix",
                                    f"🛠 원인 확인됨 · {project}\n\"{task.title}\"\n"
                                    f"원인: {str(choice.get('cause'))[:200]}\n"
                                    f"제안: {str(choice.get('fix'))[:200]}\n"
                                    f"고칠까요? {link}", task.id))
                continue
            event = db.scalar(select(Event).where(Event.task_id == task.id, Event.level == "error")
                              .order_by(Event.id.desc()).limit(1))
            if event is not None:
                alerts.append(Alert(f"task:{task.id}:problem:{event.id}", "problem",
                                    f"🔴 문제 · {project}\n\"{task.title}\"\n{event.message}\n{link}", task.id))
    return alerts


def _node_alerts(db: Session, now: datetime, offline_after: int, base_url: str) -> list[Alert]:
    alerts = []
    for node in db.scalars(select(Node).where(Node.disabled.is_(False))):
        if node_state(node.last_seen_at, now, offline_after) == "offline":
            alerts.append(Alert(f"node:{node.name}:offline", "node",
                                f"🔴 머신 연결 끊김 · {node.name}\n에이전트 신호가 멈췄습니다.\n{base_url}/"))
        for provider, name in PROVIDER_NAMES.items():
            if login_badge((node.logins or {}).get(provider)) == "logged_out":
                alerts.append(Alert(f"node:{node.name}:{provider}:logout", "node",
                                    f"🔴 {name} 로그아웃 · {node.name}\n다시 로그인해야 작업이 돕니다.\n{base_url}/"))
    return alerts


def _send(db: Session, alert: Alert, sender: Sender, now: datetime) -> None:
    row = Notification(dedup_key=alert.key, event=alert.event, task_id=alert.task_id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return
    try:
        sender(alert.text)
    except Exception as exc:  # delivery failure must not stop the scheduler; retried next tick
        log.warning("telegram send failed for %s: %s", alert.key, exc)
        db.delete(row)
        db.commit()
        return
    row.sent_at = now
    db.commit()


def deliver(db: Session, now: datetime, sender: Sender, base_url: str, offline_after_seconds: int) -> None:
    alerts = _task_alerts(db, base_url) + _node_alerts(db, now, offline_after_seconds, base_url)
    active = {a.key for a in alerts}
    db.execute(delete(Notification).where(Notification.dedup_key.startswith(CONDITION_PREFIX),
                                          Notification.dedup_key.not_in(active)))
    db.commit()
    known = set(db.scalars(select(Notification.dedup_key).where(Notification.dedup_key.in_(active))))
    for alert in alerts:
        if alert.key not in known:
            _send(db, alert, sender, now)


def telegram_sender(token: str, chat_id: str, topic_id: str | None, opener=urllib.request.urlopen) -> Sender:
    def send(text: str) -> None:
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if topic_id:
            payload["message_thread_id"] = int(topic_id)
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with opener(req, timeout=15) as resp:
            if not json.loads(resp.read()).get("ok"):
                raise RuntimeError("telegram returned ok=false")
    return send
