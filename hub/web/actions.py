import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from hub.db.models import Task
from hub.db.session import get_db
from hub.services import tasks
from hub.web.auth import is_admin

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]
CSRF_KEY = "csrf"


def csrf_token(request: Request) -> str:
    if CSRF_KEY not in request.session:
        request.session[CSRF_KEY] = secrets.token_urlsafe(32)
    return request.session[CSRF_KEY]


def _guard(request: Request, db: Session, task_id: int, csrf: str) -> Task:
    if not is_admin(request):
        raise HTTPException(401, "login required")
    expected = request.session.get(CSRF_KEY)
    if not expected or not secrets.compare_digest(expected, csrf):
        raise HTTPException(403, "invalid form token")
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    return task


def _run(action, *args) -> None:
    try:
        action(*args)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _now() -> datetime:
    return datetime.now(timezone.utc)


Csrf = Annotated[str, Form(max_length=128)]


@router.post("/tasks/{task_id}/approve-start")
def approve_start(task_id: int, request: Request, db: Db, csrf: Csrf):
    task = _guard(request, db, task_id, csrf)
    _run(tasks.approve_start, db, task, _now())
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@router.post("/tasks/{task_id}/approve-result")
def approve_result(task_id: int, request: Request, db: Db, csrf: Csrf,
                   option: Annotated[str, Form(max_length=8)] = "stop"):
    task = _guard(request, db, task_id, csrf)
    if task.status != "review":
        raise HTTPException(409, "결과 검토 상태가 아닙니다")
    index = None if option == "stop" else int(option) if option.isdigit() else -1
    try:
        follow = tasks.approve_result(db, task, index, _now())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/tasks/{follow.id if follow else task_id}", status_code=303)


@router.post("/tasks/{task_id}/cancel")
def cancel(task_id: int, request: Request, db: Db, csrf: Csrf):
    task = _guard(request, db, task_id, csrf)
    _run(tasks.cancel, db, task, _now())
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@router.post("/tasks/{task_id}/retry")
def retry(task_id: int, request: Request, db: Db, csrf: Csrf):
    task = _guard(request, db, task_id, csrf)
    _run(tasks.retry_problem, db, task, _now())
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)
