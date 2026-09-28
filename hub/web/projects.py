import re
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hub.api.envelope import ok
from hub.db.models import Node, Project, Task, Upload
from hub.db.session import get_db
from hub.services import tasks, uploads
from hub.web.actions import CSRF_KEY, csrf_token
from hub.web.auth import is_admin
from hub.web.routes import templates

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
MAX_FILES_PER_REQUEST = 20
Text = lambda n: Annotated[str, Form(max_length=n)]  # noqa: E731


def _admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(401, "login required")


def _check_csrf(request: Request, token: str) -> None:
    expected = request.session.get(CSRF_KEY)
    if not expected or not secrets.compare_digest(expected, token or ""):
        raise HTTPException(403, "invalid form token")


def _project(db: Session, slug: str) -> Project:
    project = db.scalar(select(Project).where(Project.slug == slug))
    if project is None:
        raise HTTPException(404, "project not found")
    return project


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/projects/new", response_class=HTMLResponse)
def new_project(request: Request, db: Db):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=303)
    nodes = db.scalars(select(Node).where(Node.disabled.is_(False)).order_by(Node.name)).all()
    return templates.TemplateResponse(request, "project_new.html", {"nodes": nodes, "csrf": csrf_token(request)})


@router.post("/projects")
def create_project(request: Request, db: Db, csrf: Text(128), slug: Text(63), name: Text(200), node: Text(64),
                   workdir: Text(300) = "", context: Text(20000) = "", plan: Text(200000) = ""):
    _admin(request)
    _check_csrf(request, csrf)
    slug = slug.strip()
    if not SLUG_RE.fullmatch(slug):
        raise HTTPException(422, "프로젝트 ID는 영문 소문자·숫자·하이픈만 쓸 수 있습니다")
    if db.scalar(select(Node).where(Node.name == node, Node.disabled.is_(False))) is None:
        raise HTTPException(422, "머신을 선택하세요")
    workdir = workdir.strip() or f"~/research_projects/{slug}"
    if not (workdir.startswith("/") or workdir.startswith("~/")) or ".." in workdir.split("/"):
        raise HTTPException(422, "작업 폴더는 / 또는 ~/ 로 시작하는 경로여야 합니다")
    project = Project(slug=slug, name=name.strip() or slug, workdir=workdir, node_selector=[f"node:{node}"],
                      context=context.strip() or None)
    db.add(project)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "같은 ID의 프로젝트가 이미 있습니다") from exc
    if plan.strip():
        uploads.add_upload(db, project, "plan.md", plan.encode())
    return RedirectResponse(f"/projects/{slug}", status_code=303)


@router.get("/projects/{slug}", response_class=HTMLResponse)
def project_page(slug: str, request: Request, db: Db):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=303)
    project = _project(db, slug)
    task_rows = db.scalars(select(Task).where(Task.project_id == project.id).order_by(Task.id.desc())).all()
    files = db.scalars(select(Upload).where(Upload.project_id == project.id).order_by(Upload.id.desc())).all()
    active = next((t for t in task_rows if t.status not in ("done", "cancelled")), None)
    return templates.TemplateResponse(request, "project.html", {
        "p": project, "tasks": task_rows, "files": files, "active": active, "csrf": csrf_token(request),
        "node": project.node_selector[0].removeprefix("node:") if project.node_selector else "-",
        "now": _now(), "tz": request.app.state.settings.timezone})


@router.post("/projects/{slug}/uploads")
async def upload_files(slug: str, request: Request, db: Db, files: list[UploadFile]):
    _admin(request)
    _check_csrf(request, request.headers.get("X-CSRF-Token", ""))
    project = _project(db, slug)
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(413, f"한 번에 {MAX_FILES_PER_REQUEST}개까지 올릴 수 있습니다")
    saved = []
    for item in files:
        data = await item.read(uploads.MAX_BYTES + 1)
        try:
            upload = uploads.add_upload(db, project, item.filename or "", data)
        except ValueError as exc:
            raise HTTPException(413 if len(data) > uploads.MAX_BYTES else 422, str(exc)) from exc
        saved.append({"id": upload.id, "path": upload.path, "size": upload.size})
    return ok(saved)


@router.post("/projects/{slug}/tasks")
def add_task(slug: str, request: Request, db: Db, csrf: Text(128), title: Text(200), objective: Text(8000),
             criteria: Text(4000) = "", start: Text(4) = ""):
    _admin(request)
    _check_csrf(request, csrf)
    project = _project(db, slug)
    try:
        task = tasks.create_task(db, project, title.strip(), objective.strip(), criteria.strip() or None)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "이 프로젝트에는 진행 중인 작업이 있습니다(프로젝트당 1개)") from exc
    if start:
        tasks.approve_start(db, task, _now())
    return RedirectResponse(f"/tasks/{task.id}", status_code=303)


@router.post("/projects/{slug}/settings")
def update_settings(slug: str, request: Request, db: Db, csrf: Text(128), name: Text(200), context: Text(20000) = ""):
    _admin(request)
    _check_csrf(request, csrf)
    project = _project(db, slug)
    project.name, project.context = name.strip() or project.name, context.strip() or None
    db.commit()
    return RedirectResponse(f"/projects/{slug}", status_code=303)
