from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from hub.api.envelope import ok
from hub.db.session import get_db
from hub.security import verify_password
from hub.services import task_detail
from hub.services.overview import build_overview
from hub.web import formatting
from hub.web.auth import SESSION_KEY, client_key, is_admin

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals.update(
    ago=formatting.ago, local_time=formatting.local_time, pct_level=formatting.pct_level,
    STATUS=formatting.STATUS_LABELS, STEP=task_detail.STEP_LABELS, NODE=formatting.NODE_LABELS, STEP_STATUS=formatting.STEP_STATUS, LOGIN=formatting.LOGIN_LABELS,
)
Db = Annotated[Session, Depends(get_db)]


def _overview_context(request: Request, db: Session) -> dict:
    settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    return {"view": build_overview(db, now, settings.offline_after_seconds), "now": now, "tz": settings.timezone}


@router.get("/healthz")
def healthz(db: Db) -> dict:
    db.execute(text("SELECT 1"))
    return ok({"db": "ok"})


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login(request: Request, password: Annotated[str, Form(max_length=256)]):
    limiter, key = request.app.state.login_limiter, client_key(request)
    if limiter.blocked(key):
        return templates.TemplateResponse(request, "login.html",
                                          {"error": "시도가 너무 많습니다. 5분 뒤 다시 시도하세요."}, status_code=429)
    if not verify_password(password, request.app.state.settings.admin_password_hash):
        limiter.record_failure(key)
        return templates.TemplateResponse(request, "login.html", {"error": "비밀번호가 틀렸습니다."},
                                          status_code=401)
    limiter.reset(key)
    request.session.clear()
    request.session[SESSION_KEY] = True
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
def overview(request: Request, db: Db):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "overview.html", _overview_context(request, db))


@router.get("/partials/overview", response_class=HTMLResponse)
def overview_partial(request: Request, db: Db):
    if not is_admin(request):
        raise HTTPException(401, "login required")
    return templates.TemplateResponse(request, "_overview.html", _overview_context(request, db))


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_page(task_id: int, request: Request, db: Db):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=303)
    detail = task_detail.load(db, task_id)
    if detail is None:
        raise HTTPException(404, "task not found")
    settings = request.app.state.settings
    return templates.TemplateResponse(request, "task.html", {"d": detail, "now": datetime.now(timezone.utc),
                                                             "tz": settings.timezone})
