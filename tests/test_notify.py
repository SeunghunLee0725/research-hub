from datetime import timedelta

import pytest

from hub.db.models import Event, Node, Notification, Project, Task
from hub.services import notify
from tests.conftest import NOW


class FakeSender:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def __call__(self, text):
        if self.fail:
            raise OSError("telegram down")
        self.sent.append(text)


@pytest.fixture()
def project(db):
    p = Project(slug="plasma-kg", name="플라즈마 KG", workdir="/w")
    db.add(p)
    db.commit()
    return p


def _deliver(db, sender, now=NOW):
    return notify.deliver(db, now, sender, "http://hub:8020", offline_after_seconds=90)


def test_start_approval_and_review_are_sent_once(db, project):
    task = Task(project_id=project.id, title="검색 회귀 조사", objective="o", status="proposed")
    db.add(task)
    db.commit()
    sender = FakeSender()
    _deliver(db, sender)
    _deliver(db, sender)
    assert len(sender.sent) == 1
    assert "승인 필요" in sender.sent[0] and "검색 회귀 조사" in sender.sent[0]
    assert "http://hub:8020/tasks/" in sender.sent[0]

    task.status, task.result_card = "review", {"conclusion": "recall 0.93 회복"}
    db.commit()
    _deliver(db, sender)
    assert len(sender.sent) == 2 and "recall 0.93 회복" in sender.sent[1]


def test_each_new_problem_is_sent(db, project):
    task = Task(project_id=project.id, title="t", objective="o", status="problem")
    db.add(task)
    db.commit()
    db.add(Event(task_id=task.id, level="error", message="plan 단계 3회 실패"))
    db.commit()
    sender = FakeSender()
    _deliver(db, sender)
    assert "문제" in sender.sent[0] and "plan 단계 3회 실패" in sender.sent[0]
    db.add(Event(task_id=task.id, level="error", message="다시 실패"))
    db.commit()
    _deliver(db, sender)
    assert len(sender.sent) == 2


def test_offline_node_alerts_once_and_rearms_after_recovery(db, project):
    node = Node(name="spark-a", token_hash="a" * 64, last_seen_at=NOW - timedelta(minutes=5))
    db.add(node)
    db.commit()
    sender = FakeSender()
    _deliver(db, sender)
    _deliver(db, sender)
    assert len(sender.sent) == 1 and "spark-a" in sender.sent[0]
    node.last_seen_at = NOW
    db.commit()
    _deliver(db, sender)
    node.last_seen_at = NOW - timedelta(minutes=5)
    db.commit()
    _deliver(db, sender)
    assert len(sender.sent) == 2


def test_logged_out_model_alerts(db, project):
    db.add(Node(name="spark-a", token_hash="a" * 64, last_seen_at=NOW,
                logins={"claude": {"logged_in": False}, "codex": {"logged_in": True}}))
    db.commit()
    sender = FakeSender()
    _deliver(db, sender)
    assert len(sender.sent) == 1 and "Claude" in sender.sent[0] and "로그아웃" in sender.sent[0]


def test_failed_send_is_retried_later(db, project):
    db.add(Task(project_id=project.id, title="t", objective="o", status="proposed"))
    db.commit()
    _deliver(db, FakeSender(fail=True))
    assert db.query(Notification).count() == 0
    sender = FakeSender()
    _deliver(db, sender)
    assert len(sender.sent) == 1


def test_telegram_sender_builds_request():
    calls = []

    class Resp:
        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        calls.append(req)
        return Resp()

    send = notify.telegram_sender("TOKEN", "-100", "7", opener=opener)
    send("hello")
    assert calls[0].full_url == "https://api.telegram.org/botTOKEN/sendMessage"
    assert b'"message_thread_id": 7' in calls[0].data and b'"chat_id": "-100"' in calls[0].data
