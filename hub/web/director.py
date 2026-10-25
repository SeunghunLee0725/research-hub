"""Researcher-facing director pages: confirm the charter, answer the director, hand a project over."""
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.db.models import DirectorDecision, Project
from hub.db.session import get_db
from hub.services import director
from hub.web.actions import CSRF_KEY
from hub.web.auth import is_admin

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]
Text = lambda n: Annotated[str, Form(max_length=n)]  # noqa: E731
STATUS_WORDS = {"완료": "done", "done": "done", "제외": "dropped", "dropped": "dropped", "진행": "open", "open": "open"}
STATUS_LABEL = {"done": "완료", "dropped": "제외", "open": "진행"}


def _guard(request: Request, csrf: str) -> None:
    if not is_admin(request):
        raise HTTPException(401, "login required")
    expected = request.session.get(CSRF_KEY)
    if not expected or not secrets.compare_digest(expected, csrf or ""):
        raise HTTPException(403, "invalid form token")


def _project(db: Session, slug: str) -> Project:
    project = db.scalar(select(Project).where(Project.slug == slug))
    if project is None:
        raise HTTPException(404, "project not found")
    return project


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def milestones_text(charter: dict | None) -> str:
    rows = (charter or {}).get("milestones", [])
    return "\n".join(f"{m['id']} | {m['title']} | {m['done_when']} | {STATUS_LABEL[m['status']]}" for m in rows)


def parse_milestones(text: str) -> list[dict]:
    """One per line: "M1 | 제목 | 완료 조건 | 진행/완료/제외" (the status may be left out)."""
    out = []
    for n, line in enumerate(_lines(text), start=1):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            raise ValueError(f"마일스톤 {n}번째 줄은 'M1 | 제목 | 완료 조건 | 상태' 형식이어야 합니다")
        status = STATUS_WORDS.get(parts[3] if len(parts) > 3 and parts[3] else "open")
        if status is None:
            raise ValueError(f"마일스톤 {n}번째 줄 상태는 진행·완료·제외 중 하나입니다")
        out.append({"id": parts[0], "title": parts[1], "done_when": parts[2], "status": status})
    return out


@router.post("/projects/{slug}/charter")
def confirm_charter(slug: str, request: Request, db: Db, csrf: Text(128), question: Text(1000),
                    milestones: Text(8000), max_tasks: Text(4), deliverables: Text(4000) = "",
                    boundaries: Text(6000) = "", max_run_hours: Text(8) = "", deadline: Text(10) = ""):
    _guard(request, csrf)
    project = _project(db, slug)
    try:
        charter = {"question": question.strip(), "milestones": parse_milestones(milestones),
                   "deliverables": _lines(deliverables), "boundaries": _lines(boundaries),
                   "budget": {"max_tasks": int(max_tasks), "max_run_hours": float(max_run_hours) if max_run_hours.strip() else None,
                              "deadline": deadline.strip() or None}}
        director.confirm_charter(db, project, charter, _now())
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, f"헌장 형식 오류: {exc}"[:500]) from exc
    return RedirectResponse(f"/projects/{slug}#director", status_code=303)


@router.post("/projects/{slug}/director")
def toggle_director(slug: str, request: Request, db: Db, csrf: Text(128), enabled: Text(1) = ""):
    _guard(request, csrf)
    project = _project(db, slug)
    project.director_enabled = enabled == "1"
    db.commit()
    return RedirectResponse(f"/projects/{slug}#director", status_code=303)


@router.post("/projects/{slug}/kickoff")
def kickoff(slug: str, request: Request, db: Db, csrf: Text(128)):
    _guard(request, csrf)
    try:
        task = director.kickoff(db, _project(db, slug))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/tasks/{task.id}", status_code=303)


@router.post("/director/{decision_id}/answer")
def answer(decision_id: int, request: Request, db: Db, csrf: Text(128), choice: Text(300), comment: Text(2000) = "",
           extend_tasks: Text(4) = "", extend_hours: Text(8) = "", deadline: Text(10) = ""):
    _guard(request, csrf)
    row = db.get(DirectorDecision, decision_id, with_for_update=True)  # 두 번 눌러도 후속 작업은 하나
    if row is None:
        raise HTTPException(404, "decision not found")
    try:
        extend = {"tasks": int(extend_tasks) if extend_tasks.strip() else 0,
                  "run_hours": float(extend_hours) if extend_hours.strip() else 0,
                  "deadline": deadline.strip() or None}
        director.answer(db, row, choice.strip(), comment.strip(), _now(), extend)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(409, str(exc)[:500]) from exc
    return RedirectResponse(f"/tasks/{row.task_id}", status_code=303)
