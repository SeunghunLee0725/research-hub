"""이어서 시작한 작업도 승인 대기 행을 갖고, 한 작업의 문제로 스케줄러 전체가 멈추지 않는다."""
import pytest

from hub.db.models import Approval, Event, Node, Project, Task
from hub.services import scheduler, tasks
from tests.conftest import NOW


@pytest.fixture()
def world(db):
    node = Node(name="spark-2588", token_hash="a" * 64, labels=["claude", "codex"])
    first = Project(slug="af3", name="산화 잔기", workdir="/w", node_selector=["node:spark-2588"])
    second = Project(slug="kg", name="플라즈마 KG", workdir="/w2", node_selector=["node:spark-2588"])
    db.add_all([node, first, second])
    db.commit()
    return node, first, second


def test_a_resumed_task_can_be_approved(db, world):
    _, project, _ = world
    previous = tasks.create_task(db, project, "1단계", "예측한다", "csv 4종")
    tasks.cancel(db, previous, NOW)

    resumed = tasks.resume_from(db, project, NOW)
    row = db.query(Approval).filter(Approval.task_id == resumed.id, Approval.kind == "start").one()
    assert row.status == "pending"

    tasks.approve_start(db, resumed, NOW)
    db.refresh(resumed)
    assert resumed.status == "approved"


def test_a_resumed_task_starts_by_itself_when_the_agent_approves(db, world):
    _, project, _ = world
    previous = tasks.create_task(db, project, "1단계", "예측한다", "csv 4종")
    tasks.cancel(db, previous, NOW)
    resumed = tasks.resume_from(db, project, NOW)

    scheduler.tick(db, NOW)

    db.refresh(resumed)
    assert resumed.status == "running"
    row = db.query(Approval).filter(Approval.task_id == resumed.id, Approval.kind == "start").one()
    assert row.decided_by == "agent:auto"


def test_one_broken_task_does_not_stop_the_other_projects(db, world, monkeypatch):
    _, first, second = world
    broken = tasks.create_task(db, first, "고장", "o", None)
    healthy = tasks.create_task(db, second, "정상", "o", None)
    real = tasks.approve_start

    def fail_on_broken(session, task, now, **kwargs):
        if task.id == broken.id:
            raise RuntimeError("이 작업만 터진다")
        return real(session, task, now, **kwargs)

    monkeypatch.setattr(scheduler.task_service, "approve_start", fail_on_broken)
    scheduler.tick(db, NOW)

    db.refresh(broken)
    db.refresh(healthy)
    assert healthy.status == "running"
    assert broken.status == "proposed"
    assert any(e.level == "error" for e in db.query(Event).filter(Event.task_id == broken.id))


def test_a_proposed_task_that_lost_its_approval_row_is_repaired(db, world):
    """The row is what a person clicks; a proposed task without one can never be started."""
    _, project, _ = world
    task = tasks.create_task(db, project, "행이 사라진 작업", "o", None)
    db.query(Approval).filter(Approval.task_id == task.id).delete()
    db.commit()

    tasks.approve_start(db, task, NOW)

    db.refresh(task)
    assert task.status == "approved"
    row = db.query(Approval).filter(Approval.task_id == task.id, Approval.kind == "start").one()
    assert row.status == "approved"
