from datetime import timedelta

from hub.web.formatting import ago, local_time, pct_level
from tests.conftest import NOW


def test_ago():
    assert ago(None, NOW) == "-"
    assert ago(NOW - timedelta(seconds=5), NOW) == "5초 전"
    assert ago(NOW - timedelta(minutes=3), NOW) == "3분 전"
    assert ago(NOW - timedelta(hours=2), NOW) == "2시간 전"
    assert ago(NOW - timedelta(days=3), NOW) == "3일 전"


def test_local_time_converts_to_kst():
    assert local_time("2026-09-28T03:00:00+00:00", "Asia/Seoul") == "09/28 12:00"
    assert local_time(NOW, "Asia/Seoul") == "09/28 12:00"
    assert local_time(None, "Asia/Seoul") == "-"


def test_pct_level():
    assert [pct_level(v) for v in (None, 10, 60, 85)] == ["idle", "ok", "warn", "bad"]
