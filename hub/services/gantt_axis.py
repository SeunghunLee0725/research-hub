"""Time axis for the Gantt view: tick positions and chart width. Pure functions, no I/O."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = "월화수목금토일"
MIN_WIDTH_PX = 960
MAX_WIDTH_PX = 8000
LEAD_LABEL_PX = 80  # room the leading date label needs before the next tick label


@dataclass(frozen=True)
class Tick:
    left: float
    label: str
    major: bool


@dataclass(frozen=True)
class Shade:
    left: float
    width: float


@dataclass(frozen=True)
class Axis:
    start: datetime
    end: datetime
    width_px: int
    ticks: tuple[Tick, ...]
    shades: tuple[Shade, ...]  # weekends, so a bar's weekday can be read at a glance
    now_pct: float | None

    def pct(self, value: datetime) -> float:
        return pct_between(self.start, self.end, value)


def pct_between(start: datetime, end: datetime, value: datetime) -> float:
    span = (end - start).total_seconds()
    return max(0.0, min(100.0, (value - start).total_seconds() / span * 100)) if span > 0 else 0.0


def _width(days: float) -> int:
    # Steps last minutes to an hour: a day view gives an hour 100px, a week view 15px.
    per_day = 2400 if days <= 1.5 else 360 if days <= 10 else 110
    return int(max(MIN_WIDTH_PX, min(MAX_WIDTH_PX, days * per_day)))


def _local_midnight(value: datetime, tz: ZoneInfo) -> datetime:
    local = value.astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _hour_ticks(start: datetime, end: datetime, tz: ZoneInfo, step_hours: int) -> list[tuple[datetime, str, bool]]:
    t = _local_midnight(start, tz)
    out = []
    while t <= end:
        if t >= start:
            major = t.hour == 0
            label = f"{t.month}/{t.day}({WEEKDAYS[t.weekday()]})" if major else f"{t.hour}시"
            out.append((t, label, major))
        t = (t + timedelta(hours=step_hours)).astimezone(tz)
    return out


def _day_ticks(start: datetime, end: datetime, tz: ZoneInfo, label_every_day: bool) -> list[tuple[datetime, str, bool]]:
    t = _local_midnight(start, tz)
    out = []
    while t <= end:
        if t >= start:
            monday = t.weekday() == 0
            major = monday if not label_every_day else True
            if label_every_day:
                label = f"{t.month}/{t.day}({WEEKDAYS[t.weekday()]})"
            else:
                label = f"{t.month}/{t.day}" if monday or t.day == 1 else str(t.day)
            out.append((t, label, major))
        t = _local_midnight(t + timedelta(hours=26), tz)
    return out


def _weekends(start: datetime, end: datetime, tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    t = _local_midnight(start, tz)
    out = []
    while t <= end:
        nxt = _local_midnight(t + timedelta(hours=26), tz)
        if t.weekday() >= 5:
            out.append((max(t, start), min(nxt, end)))
        t = nxt
    return out


def build_axis(start: datetime, end: datetime, now: datetime, tz_name: str) -> Axis:
    tz = ZoneInfo(tz_name)
    days = (end - start).total_seconds() / 86400
    if days <= 1.5:
        raw = _hour_ticks(start, end, tz, 1)
    elif days <= 10:
        raw = _hour_ticks(start, end, tz, 6)
    else:
        raw = _day_ticks(start, end, tz, label_every_day=False)
    width = _width(days)
    ticks = _with_lead_label([Tick(pct_between(start, end, t), label, major) for t, label, major in raw],
                             start.astimezone(tz), width)
    shades = tuple(Shade(pct_between(start, end, a), pct_between(start, end, b) - pct_between(start, end, a))
                   for a, b in _weekends(start, end, tz)) if days > 1.5 else ()
    now_pct = pct_between(start, end, now) if start <= now <= end else None
    return Axis(start, end, width, ticks, shades, now_pct)


def _with_lead_label(ticks: list[Tick], local_start: datetime, width_px: int) -> tuple[Tick, ...]:
    """The window rarely starts at midnight: name the first, partial day at the left edge too."""
    if ticks and ticks[0].left == 0 and ticks[0].major:
        return tuple(ticks)
    room = LEAD_LABEL_PX / width_px * 100
    lead = Tick(0.0, f"{local_start.month}/{local_start.day}({WEEKDAYS[local_start.weekday()]})", False)
    return (lead, *(t for t in ticks if t.major or t.left >= room))
