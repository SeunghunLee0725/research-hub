from datetime import date, timedelta

from hub.db.models import Node, Project, Step, Task
from hub.services import timeline
from hub.services.gantt_axis import build_axis
from tests.conftest import NOW

TZ = "Asia/Seoul"
H = timedelta(hours=1)


def _seed(db):
    dbb1 = Node(name="spark-dbb1", token_hash="a" * 64)
    a4000 = Node(name="shlee-a4000", token_hash="b" * 64)
    grant = Project(slug="drug", name="마약탐지", workdir="/w", kind="research",
                    starts_on=date(2026, 4, 1), ends_on=date(2026, 12, 31), node_selector=["node:spark-dbb1"])
    short = Project(slug="ion", name="이온소스", workdir="/w", kind="short", ends_on=date(2026, 10, 3))
    db.add_all([dbb1, a4000, grant, short])
    db.commit()
    done = Task(project_id=grant.id, title="X3a 라벨", objective="o", status="done", node_id=dbb1.id,
                created_at=NOW - 6 * H, updated_at=NOW - 2 * H)
    running = Task(project_id=short.id, title="음극 PIC", objective="o", status="running", node_id=a4000.id,
                   created_at=NOW - 3 * H, updated_at=NOW - H)
    old = Task(project_id=grant.id, title="오래된 작업", objective="o", status="done",
               created_at=NOW - timedelta(days=20), updated_at=NOW - timedelta(days=19))
    db.add_all([done, running, old])
    db.commit()
    db.add_all([
        Step(task_id=done.id, seq=1, kind="plan", model="claude", status="succeeded", node_id=dbb1.id,
             started_at=NOW - 5 * H, ended_at=NOW - 4 * H, output={"_meta": {"model": "claude-opus-5-5"}}),
        Step(task_id=done.id, seq=2, kind="implement", model="codex", status="failed", attempt=1, node_id=dbb1.id,
             started_at=NOW - 4 * H, ended_at=NOW - 3.5 * H, error_class="sandbox"),
        Step(task_id=done.id, seq=2, kind="implement", model="claude", status="succeeded", attempt=2,
             node_id=dbb1.id, started_at=NOW - 3.5 * H, ended_at=NOW - 3 * H),
        Step(task_id=done.id, seq=3, kind="report", model="claude", status="succeeded", node_id=dbb1.id,
             started_at=NOW - 3 * H, ended_at=NOW - 2 * H),
        Step(task_id=running.id, seq=1, kind="plan", model="claude", status="succeeded", node_id=a4000.id,
             started_at=NOW - 2.5 * H, ended_at=NOW - 2 * H),
        Step(task_id=running.id, seq=2, kind="run", model="none", status="running", node_id=a4000.id,
             started_at=NOW - 2 * H),
        Step(task_id=running.id, seq=3, kind="analyze", model="claude", status="pending"),
    ])
    db.commit()
    return grant, short


def test_axis_scales_ticks_to_the_span():
    day = build_axis(NOW - 24 * H, NOW, NOW, TZ)
    assert 12 <= len(day.ticks) <= 26
    assert abs(day.now_pct - 100) < 0.01
    assert day.width_px >= 2000  # an hour must be wide enough to read step segments
    week = build_axis(NOW - timedelta(days=7), NOW, NOW, TZ)
    assert week.width_px >= 7 * 300
    assert any(t.label == "12시" for t in week.ticks)  # quarter-day marks inside each day
    month = build_axis(NOW - timedelta(days=30), NOW, NOW, TZ)
    assert sum(t.major for t in month.ticks) == 5  # one per Monday


def test_axis_labels_the_partial_first_day():
    axis = build_axis(NOW - timedelta(days=7), NOW, NOW, TZ)  # starts at 12:00 KST, mid-day
    first = axis.ticks[0]
    assert first.left == 0 and "/" in first.label


def test_window_for_named_ranges():
    start, end = timeline.window("1", NOW, earliest=None)
    assert NOW - start == timedelta(days=1) and end - NOW >= 2 * H
    start, end = timeline.window("7", NOW, earliest=None)
    assert end - NOW >= timedelta(days=1)  # the future side is drawn too, not cut at "now"
    start, _ = timeline.window("all", NOW, earliest=NOW - timedelta(days=500))
    assert NOW - start == timedelta(days=365)  # capped so one stray date cannot stretch the axis
    start, _ = timeline.window("bogus", NOW, earliest=None)
    assert NOW - start == timedelta(days=7)


def test_all_range_reaches_the_last_project_end():
    _, end = timeline.window("all", NOW, earliest=NOW - timedelta(days=10), latest=NOW + timedelta(days=90))
    assert end >= NOW + timedelta(days=90)
    _, end = timeline.window("all", NOW, earliest=None, latest=NOW + timedelta(days=5000))
    assert end - NOW <= timedelta(days=366)


def test_latest_project_end(db):
    _seed(db)
    assert timeline.latest_project_end(db, None, TZ).date() == date(2027, 1, 1)
    assert timeline.latest_project_end(db, "short", TZ).date() == date(2026, 10, 4)


def test_gantt_groups_by_kind_and_places_step_segments(db):
    _seed(db)
    start, end = NOW - 24 * H, NOW + H
    gantt = timeline.build_gantt(db, NOW, start, end, kind=None)

    assert [g.kind for g in gantt.groups] == ["research", "short"]
    (grant,) = gantt.groups[0].rows
    assert grant.name == "마약탐지" and len(grant.bars) == 1  # 20-day-old task is outside the window
    bar = grant.bars[0]
    assert bar.title == "X3a 라벨" and bar.status == "done"
    assert [s.kind for s in bar.segments] == ["plan", "implement", "implement", "report"]
    assert [s.agent for s in bar.segments] == ["claude", "codex", "claude", "claude"]
    assert bar.segments[1].status == "failed" and bar.segments[1].error_class == "sandbox"
    assert bar.segments[0].model_name == "claude-opus-5-5"
    assert 0 < bar.left < bar.segments[0].left
    assert grant.period is not None and grant.period == (0.0, 100.0)  # grant spans the whole window

    (ion,) = gantt.groups[1].rows
    run = ion.bars[0]
    assert run.status == "running" and run.segments[-1].kind == "run"
    assert run.segments[-1].end == NOW  # still running: drawn up to now
    assert all(s.kind != "analyze" for s in run.segments)  # pending, never started
    assert ion.deadline_pct is None  # deadline 10/3 is after the window ends


def test_gantt_kind_filter(db):
    _seed(db)
    gantt = timeline.build_gantt(db, NOW, NOW - 24 * H, NOW + H, kind="short")
    assert [g.kind for g in gantt.groups] == ["short"]


def test_project_without_tasks_still_shows_a_row(db):
    db.add(Project(slug="empty", name="빈 과제", workdir="/w", kind="research"))
    db.commit()
    gantt = timeline.build_gantt(db, NOW, NOW - 24 * H, NOW + H, kind=None)
    (row,) = gantt.groups[0].rows
    assert row.bars == ()


def test_agent_lanes_pack_overlapping_steps(db):
    _seed(db)
    lanes = timeline.build_agent_lanes(db, NOW, NOW - 24 * H, NOW + H, kind=None)
    keys = [(lane.node, lane.agent) for lane in lanes.lanes]
    assert ("spark-dbb1", "claude") in keys and ("spark-dbb1", "codex") in keys
    assert ("shlee-a4000", "none") in keys
    claude = next(lane for lane in lanes.lanes if lane.key == ("spark-dbb1", "claude"))
    assert len(claude.tracks) == 1  # plan, implement retry and report do not overlap
    assert {s.project for track in claude.tracks for s in track} == {"마약탐지"}


def test_overlapping_steps_go_to_separate_tracks():
    seg = lambda a, b: timeline.LaneSegment(project="p", task_id=1, title="t", kind="run", status="running",  # noqa: E731
                                            start=NOW + a * H, end=NOW + b * H, left=0, width=1, color=0,
                                            model_name=None)
    tracks = timeline.pack([seg(0, 2), seg(1, 3), seg(2, 4)])
    assert [len(t) for t in tracks] == [2, 1]
