from datetime import timedelta

from hub.db.models import Event, Node, Project, Step, Task
from hub.services import board
from tests.conftest import NOW

H = timedelta(hours=1)


def _seed(db):
    node = Node(name="shlee-a4000", token_hash="a" * 64)
    research = Project(slug="drug", name="마약탐지", workdir="/w", kind="research")
    short = Project(slug="ion", name="이온소스", workdir="/w", kind="short")
    other = Project(slug="kg", name="KG", workdir="/w", kind="research")
    db.add_all([node, research, short, other])
    db.commit()
    running = Task(project_id=short.id, title="음극 PIC", objective="o", status="running", node_id=node.id,
                   created_at=NOW - 3 * H)
    done = Task(project_id=research.id, title="라벨", objective="o", status="done", updated_at=NOW - 2 * H,
                result_card={"conclusion": "정확도 92% 로 기준 통과"})
    stale = Task(project_id=research.id, title="지난달", objective="o", status="done",
                 updated_at=NOW - timedelta(days=30))
    proposed = Task(project_id=other.id, title="새 제안", objective="o", status="proposed")
    db.add_all([running, done, stale, proposed])
    db.commit()
    db.add_all([
        Step(task_id=running.id, seq=1, kind="plan", model="claude", status="succeeded",
             started_at=NOW - 2 * H, ended_at=NOW - 1.5 * H),
        Step(task_id=running.id, seq=2, kind="implement", model="codex", status="failed", attempt=1,
             started_at=NOW - 1.5 * H, ended_at=NOW - 1.4 * H),
        Step(task_id=running.id, seq=2, kind="implement", model="claude", status="running", attempt=2,
             started_at=NOW - 1.4 * H, output=None),
        Event(task_id=running.id, message="구현 단계를 Claude 로 전환", at=NOW - 1.4 * H),
    ])
    db.commit()
    return running


def test_board_columns_by_status(db):
    _seed(db)
    view = board.build_board(db, NOW, kind=None, done_days=7)
    by_status = {c.status: c for c in view.columns}
    assert [c.status for c in view.columns] == list(board.COLUMN_ORDER)
    assert [card.title for card in by_status["running"].cards] == ["음극 PIC"]
    assert [card.title for card in by_status["proposed"].cards] == ["새 제안"]
    assert [card.title for card in by_status["done"].cards] == ["라벨"]  # 30-day-old one is outside done_days
    assert by_status["done"].cards[0].conclusion.startswith("정확도")


def test_card_shows_current_agent_and_step_chain(db):
    _seed(db)
    (card,) = board.build_board(db, NOW, kind=None, done_days=7).column("running").cards
    assert card.project == "이온소스" and card.kind == "short" and card.node == "shlee-a4000"
    assert card.current.kind == "implement" and card.current.agent == "claude" and card.current.status == "running"
    assert [(c.kind, c.agent, c.status) for c in card.chain] == [("plan", "claude", "succeeded"),
                                                                  ("implement", "claude", "running")]
    assert card.retries == 1
    assert card.last_event == "구현 단계를 Claude 로 전환"
    assert card.started_at == NOW - 2 * H


def test_board_kind_filter(db):
    _seed(db)
    view = board.build_board(db, NOW, kind="short", done_days=7)
    assert sum(len(c.cards) for c in view.columns) == 1
    assert view.counts["running"] == 1
