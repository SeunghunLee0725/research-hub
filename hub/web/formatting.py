from datetime import datetime
from zoneinfo import ZoneInfo

STATUS_LABELS = {
    "idle": ("대기", "작업 없음", "idle"),
    "proposed": ("승인 대기", "시작 승인 필요", "wait"),
    "approved": ("준비", "실행 대기열", "run"),
    "running": ("실행 중", "", "run"),
    "review": ("승인 대기", "결과 검토 필요", "wait"),
    "problem": ("문제", "확인 필요", "bad"),
}
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
