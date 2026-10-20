"""Gantt chart and status board: where every AI-run task is, over time and by status."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from hub.db.models import PROJECT_KINDS
from hub.db.session import get_db
from hub.services import board, timeline
from hub.services.gantt_axis import build_axis
from hub.web.auth import is_admin
from hub.web.routes import templates
from hub.web.viewmode import is_mobile, remember_choice

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]
RANGE_LABELS = (("1", "24시간"), ("7", "7일"), ("30", "30일"), ("all", "전체"))
DONE_DAYS = (1, 7, 30)


def _login_needed(request: Request):
    if request.query_params.get("partial"):
        raise HTTPException(401, "login required")
    return RedirectResponse("/login", status_code=303)


def _kind(request: Request) -> str | None:
    kind = request.query_params.get("kind")
    return kind if kind in PROJECT_KINDS else None


def _choice(request: Request, name: str, allowed: tuple[str, ...], default: str) -> str:
    value = request.query_params.get(name)
    return value if value in allowed else default


@router.get("/gantt", response_class=HTMLResponse)
def gantt(request: Request, db: Db):
    if not is_admin(request):
        return _login_needed(request)
    remember_choice(request)
    settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    kind = _kind(request)
    span = _choice(request, "range", tuple(k for k, _ in RANGE_LABELS), timeline.DEFAULT_RANGE)
    view = _choice(request, "view", ("project", "agent"), "project")
    start, end = timeline.window(span, now, timeline.earliest_task(db) if span == "all" else None)
    context = {"now": now, "tz": settings.timezone, "kind": kind, "range": span, "view": view,
               "ranges": RANGE_LABELS, "axis": build_axis(start, end, now, settings.timezone)}
    if view == "agent" and not is_mobile(request):
        context["lanes"] = timeline.build_agent_lanes(db, now, start, end, kind)
    else:
        context["gantt"] = timeline.build_gantt(db, now, start, end, kind, settings.timezone)
    name = "_gantt_body.html" if request.query_params.get("partial") else "gantt.html"
    return templates.TemplateResponse(request, name, context)


@router.get("/board", response_class=HTMLResponse)
def status_board(request: Request, db: Db):
    if not is_admin(request):
        return _login_needed(request)
    remember_choice(request)
    settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    kind = _kind(request)
    done_days = int(_choice(request, "done", tuple(str(d) for d in DONE_DAYS), "7"))
    context = {"now": now, "tz": settings.timezone, "kind": kind, "done_days": done_days, "done_choices": DONE_DAYS,
               "board": board.build_board(db, now, kind, done_days)}
    name = "_board_body.html" if request.query_params.get("partial") else "board.html"
    return templates.TemplateResponse(request, name, context)
