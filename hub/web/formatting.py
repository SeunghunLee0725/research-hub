from datetime import date, datetime
from zoneinfo import ZoneInfo

STATUS_LABELS = {
    "idle": ("대기", "작업 없음", "idle"),
    "proposed": ("승인 대기", "시작 승인 필요", "wait"),
    "approved": ("준비", "실행 대기열", "run"),
    "running": ("실행 중", "", "run"),
    "review": ("승인 대기", "결과 검토 필요", "wait"),
    "waiting_human": ("사람 작업 필요", "웹에서 입력", "wait"),
    "problem": ("문제", "확인 필요", "bad"),
    "done": ("완료", "", "ok"),
    "cancelled": ("취소", "", "idle"),
}
STEP_STATUS = {"pending": ("대기", "idle"), "leased": ("시작", "run"), "running": ("진행 중", "run"),
               "succeeded": ("완료", "ok"), "failed": ("실패", "bad"), "lost": ("끊김", "bad")}
NODE_LABELS = {"online": ("온라인", "ok"), "offline": ("오프라인", "bad"), "never": ("연결 전", "idle")}
LOGIN_LABELS = {"ok": ("로그인", "ok"), "logged_out": ("로그아웃", "bad"), "unknown": ("확인 불가", "warn")}


def ago(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "-"
    seconds = int((now - value).total_seconds())
    if seconds < 60:
        return f"{max(seconds, 0)}초 전"
    if seconds < 3600:
        return f"{seconds // 60}분 전"
    if seconds < 86400:
        return f"{seconds // 3600}시간 전"
    return f"{seconds // 86400}일 전"


def local_time(value: str | datetime | None, tz: str) -> str:
    if not value:
        return "-"
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed.astimezone(ZoneInfo(tz)).strftime("%m/%d %H:%M")


def pct_level(value: float | None) -> str:
    if value is None:
        return "idle"
    return "bad" if value >= 85 else "warn" if value >= 60 else "ok"


def by_priority(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: item.get("priority") or 99)


KIND_LABELS = {"research": "연구과제", "short": "단기 프로젝트"}
AGENT_LABELS = {"claude": "Claude", "codex": "Codex", "none": "머신 실행"}
BOARD_LABELS = {"proposed": "시작 승인 대기", "approved": "대기열", "running": "실행 중", "waiting_human": "사람 작업",
                "review": "결과 검토", "problem": "문제", "done": "완료", "cancelled": "취소"}


def duration(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return "-"
    minutes = max(0, int((end - start).total_seconds() // 60))
    if minutes < 60:
        return f"{max(minutes, 1) if end > start else 0}분"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}시간 {minutes}분" if minutes else f"{hours}시간"
    days, hours = divmod(hours, 24)
    return f"{days}일 {hours}시간" if hours else f"{days}일"


def d_day(ends_on: date | None, today: date) -> str | None:
    if ends_on is None:
        return None
    left = (ends_on - today).days
    if left > 0:
        return f"D-{left}"
    return "D-day" if left == 0 else f"마감 {-left}일 지남"


def period_text(starts_on: date | None, ends_on: date | None, today: date) -> str:
    if not (starts_on or ends_on):
        return "-"
    span = f"{starts_on:%y.%m.%d} ~ " if starts_on else "~ "
    span += f"{ends_on:%y.%m.%d}" if ends_on else ""
    return f"{span} · {d_day(ends_on, today)}" if ends_on else span


def local_date(value: datetime, tz: str) -> date:
    return value.astimezone(ZoneInfo(tz)).date()
