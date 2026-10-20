"""Phone site (/m) tuned for a Galaxy S25: bottom tabs, one column, large touch targets."""
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from hub.db.models import PROJECT_KINDS
from hub.db.session import get_db
from hub.services import board, timeline
from hub.services.overview import build_overview, done_since
from hub.web.auth import is_admin
from hub.web.formatting import local_date
from hub.web.routes import templates

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]
RANGES = (("1", "24시간"), ("7", "7일"), ("30", "30일"))
MANIFEST = {
    "name": "Research Hub", "short_name": "Hub", "start_url": "/m", "scope": "/", "display": "standalone",
    "orientation": "portrait", "background_color": "#111418", "theme_color": "#1a1f26", "lang": "ko",
    "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}


def _guard(request: Request):
    if is_admin(request):
        return None
    if request.query_params.get("partial"):
        raise HTTPException(401, "login required")
    return RedirectResponse("/login", status_code=303)


def _render(request: Request, page: str, context: dict):
    settings = request.app.state.settings
    base = {"now": context.get("now") or datetime.now(timezone.utc), "tz": settings.timezone}
    name = f"m/_{page}.html" if request.query_params.get("partial") else f"m/{page}.html"
    return templates.TemplateResponse(request, name, {**base, **context})


@router.get("/manifest.webmanifest")
def manifest():
    return JSONResponse(MANIFEST, media_type="application/manifest+json")


@router.get("/m", response_class=HTMLResponse)
def home(request: Request, db: Db):
    if (redirect := _guard(request)) is not None:
        return redirect
    settings, now = request.app.state.settings, datetime.now(timezone.utc)
    return _render(request, "home", {"now": now, "view": build_overview(db, now, settings.offline_after_seconds),
                                     "done_today": done_since(db, now - timedelta(hours=24))})


@router.get("/m/board", response_class=HTMLResponse)
def mobile_board(request: Request, db: Db):
    if (redirect := _guard(request)) is not None:
        return redirect
    now = datetime.now(timezone.utc)
    return _render(request, "board", {"now": now, "board": board.build_board(db, now, None, 7)})


def strip(bar: timeline.TaskBar) -> list[dict]:
    """Segment positions inside the task's own span, so a one-hour task fills the phone width."""
    span = max((bar.end - bar.start).total_seconds(), 1)
    out = []
    for s in bar.segments:
        left = max(0.0, (s.start - bar.start).total_seconds() / span * 100)
        right = min(100.0, (s.end - bar.start).total_seconds() / span * 100)
        out.append({"seg": s, "left": round(left, 2), "width": round(max(right - left, 0.8), 2)})
    return out


def _timeline_days(gantt: timeline.Gantt, tz: str) -> list[tuple]:
    items = [(row, bar) for g in gantt.groups for row in g.rows for bar in row.bars]
    items.sort(key=lambda rb: rb[1].start, reverse=True)
    days: dict = {}
    for row, bar in items:
        days.setdefault(local_date(bar.start, tz), []).append((row, bar, strip(bar)))
    return list(days.items())


@router.get("/m/timeline", response_class=HTMLResponse)
def mobile_timeline(request: Request, db: Db):
    if (redirect := _guard(request)) is not None:
        return redirect
    settings, now = request.app.state.settings, datetime.now(timezone.utc)
    span = request.query_params.get("range")
    span = span if span in dict(RANGES) else "7"
    kind = request.query_params.get("kind")
    kind = kind if kind in PROJECT_KINDS else None
    start, end = timeline.window(span, now, None)
    gantt = timeline.build_gantt(db, now, start, end, kind, settings.timezone)
    return _render(request, "timeline", {"now": now, "range": span, "kind": kind, "ranges": RANGES,
                                         "days": _timeline_days(gantt, settings.timezone)})


@router.get("/m/projects", response_class=HTMLResponse)
def mobile_projects(request: Request, db: Db):
    if (redirect := _guard(request)) is not None:
        return redirect
    settings, now = request.app.state.settings, datetime.now(timezone.utc)
    return _render(request, "projects", {"now": now, "view": build_overview(db, now, settings.offline_after_seconds)})
