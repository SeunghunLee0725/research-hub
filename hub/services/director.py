"""The project director: at each result gate it decides the next direction within the confirmed charter.

The researcher is asked only when the budget runs out, a new result contradicts a settled conclusion,
the director proposes closing the project, or a result is held."""
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hub.db.models import TASK_FINISHED, DirectorDecision, Event, Project, Step, Task
from hub.services import router
from hub.services import tasks as task_service
from hub.services.director_prompts import DIRECTION_PATH, build_charter_prompt, build_direct_prompt
from hub.services.director_schemas import Charter
from hub.services.pipeline import StepOutcome, retry_decision

log = logging.getLogger("research-hub")
LOCAL_TZ = ZoneInfo("Asia/Seoul")  # 마감일은 연구자의 날짜로 판단한다
MODEL_TIMEOUT_MINUTES = 60
SESSION_LIMIT_WAIT = timedelta(minutes=30)
HISTORY = 40
JOURNAL = 10
PROGRESS_ACTIONS = ("continue", "new_task")
KICKOFF_TITLE = "디렉터 착수: 프로젝트 현황 정리"
KICKOFF_OBJECTIVE = (
    "디렉터가 프로젝트를 맡기 전에 지금까지의 상태를 정리한다. plan.md, materials/, 연구노트, 이전 작업의 결과 파일"
    "(.research-hub/steps/)을 읽고 핵심 결론(근거 수치와 파일 위치), 아직 답하지 못한 질문, 남은 일을 정리한다. "
    "새 실험·계산·코드 수정은 하지 않는다.")


def is_active(project: Project) -> bool:
    return project.director_enabled and project.auto_approve and project.closed_at is None


def _latest_step(db: Session, task: Task) -> Step | None:
    return db.scalar(select(Step).where(Step.task_id == task.id)
                     .order_by(Step.seq.desc(), Step.attempt.desc()).limit(1))


def pending_decision(db: Session, task: Task) -> DirectorDecision | None:
    if task.status != "review":
        return None
    return db.scalar(select(DirectorDecision).where(DirectorDecision.task_id == task.id,
                                                    DirectorDecision.status == "pending")
                     .order_by(DirectorDecision.id.desc()).limit(1))


def pending_for_project(db: Session, project: Project) -> list[DirectorDecision]:
    return list(db.scalars(select(DirectorDecision).join(Task, Task.id == DirectorDecision.task_id)
                           .where(DirectorDecision.project_id == project.id, DirectorDecision.status == "pending",
                                  Task.status == "review").order_by(DirectorDecision.id)))


def decisions(db: Session, project: Project, limit: int) -> list[DirectorDecision]:
    rows = db.scalars(select(DirectorDecision).where(DirectorDecision.project_id == project.id)
                      .order_by(DirectorDecision.id.desc()).limit(limit)).all()
    return list(reversed(rows))


def budget_usage(db: Session, project: Project, now: datetime) -> dict:
    budget = (project.charter or {}).get("budget") or {}
    since = project.charter_confirmed_at or now
    used = db.scalar(select(func.count()).select_from(Task).where(
        Task.project_id == project.id, Task.created_at >= since, Task.status != "cancelled")) or 0
    seconds = db.scalar(select(func.coalesce(func.sum(func.extract("epoch", Step.ended_at - Step.started_at)), 0))
                        .join(Task, Task.id == Step.task_id)
                        .where(Task.project_id == project.id, Step.kind == "run", Step.started_at >= since,
                               Step.ended_at.is_not(None)))
    usage = {"tasks_used": used, "max_tasks": budget.get("max_tasks"), "run_hours": float(seconds) / 3600,
             "max_run_hours": budget.get("max_run_hours"), "deadline": budget.get("deadline")}
    over = []
    if usage["max_tasks"] and used >= usage["max_tasks"]:
        over.append(f"작업 {used}/{usage['max_tasks']}개를 다 썼다")
    if usage["max_run_hours"] and usage["run_hours"] >= usage["max_run_hours"]:
        over.append(f"머신 실행 {usage['run_hours']:.1f}/{usage['max_run_hours']}시간을 다 썼다")
    if usage["deadline"] and now.astimezone(LOCAL_TZ).date().isoformat() > usage["deadline"]:
        over.append(f"마감 {usage['deadline']} 이 지났다")
    return {**usage, "over": over}


def stall_count(db: Session, project: Project) -> int:
    """Gates in a row where the director moved on without closing or dropping a milestone."""
    count = 0
    for row in reversed(decisions(db, project, 50)):
        if row.action not in PROGRESS_ACTIONS and row.action != "budget":
            continue
        if (row.payload or {}).get("progress"):
            break
        count += 1
    return count


def _journal(db: Session, project: Project, limit: int) -> list[str]:
    return [f"{r.at:%Y-%m-%d} #{r.task_id} {r.action}: {(r.payload or {}).get('journal') or r.reason or ''}"[:400]
            for r in decisions(db, project, limit)]


def render_direction(db: Session, project: Project, now: datetime) -> str:
    charter = project.charter or {}
    mark = {"done": "x", "dropped": "-", "open": " "}
    milestones = "\n".join(f"- [{mark.get(m.get('status'), ' ')}] {m['id']} {m['title']} — 완료 조건: {m['done_when']}"
                           for m in charter.get("milestones", []))
    usage = budget_usage(db, project, now)
    state = "확정" if project.charter_confirmed_at else "초안(연구자 확인 전)"
    return f"""# 연구 방향 — {project.name}
허브가 디렉터 판단 기록으로 만든 문서다. 직접 고치지 않는다(작업마다 다시 쓰인다). 헌장 상태: {state}

## 최종 질문
{charter.get('question', '(헌장 없음)')}

## 마일스톤
{milestones or '(없음)'}

## 산출물
{chr(10).join(f'- {d}' for d in charter.get('deliverables', [])) or '(없음)'}

## 하지 말 것
{chr(10).join(f'- {b}' for b in charter.get('boundaries', [])) or '(없음)'}

## 예산
작업 {usage['tasks_used']}/{usage['max_tasks']}개 · 머신 실행 {usage['run_hours']:.1f}시간 · 마감 {usage['deadline'] or '없음'}

## 연구 일지 (최근 30)
{chr(10).join(f'- {line}' for line in _journal(db, project, 30)) or '(없음)'}
"""


def direction_files(db: Session, project: Project, now: datetime | None = None) -> dict[str, str]:
    if not project.director_enabled or not project.charter:
        return {}
    return {DIRECTION_PATH: render_direction(db, project, now or datetime.now(timezone.utc))}


def _history(db: Session, project: Project) -> list[Task]:
    rows = db.scalars(select(Task).where(Task.project_id == project.id).order_by(Task.id.desc()).limit(HISTORY)).all()
    return list(reversed(rows))


def _answers(db: Session, task: Task) -> list[dict]:
    rows = db.scalars(select(DirectorDecision).where(DirectorDecision.task_id == task.id,
                                                     DirectorDecision.status == "answered")
                      .order_by(DirectorDecision.id)).all()
    return [{"asked": (r.payload or {}).get("question") or r.reason or r.action,
             "answer": (r.answer or {}).get("text", "")} for r in rows if r.action != "charter"]


def _new_gate_step(db: Session, task: Task, project: Project, kind: str, seq: int, now: datetime,
                   usage: dict[str, float | None]) -> Step:
    step = Step(task_id=task.id, seq=seq, kind=kind, model=router.choose_model(kind, usage), attempt=1, input={})
    db.add(step)
    db.flush()
    result_path = f"{project.workdir}/.research-hub/steps/{step.id}/result.json"
    if kind == "charter":
        prompt = build_charter_prompt(project, task, _history(db, project), result_path)
    else:
        prompt = build_direct_prompt(project, task, _history(db, project), result_path,
                                     budget=budget_usage(db, project, now), stall=stall_count(db, project),
                                     answers=_answers(db, task), journal=_journal(db, project, JOURNAL))
    step.input = {"result_path": result_path, "timeout_minutes": MODEL_TIMEOUT_MINUTES, "prompt": prompt,
                  "files": direction_files(db, project, now)}
    db.add(Event(task_id=task.id, step_id=step.id,
                 message="디렉터가 연구 헌장 초안을 쓴다" if kind == "charter" else "디렉터가 다음 방향을 판단한다"))
    return step


def _record(db: Session, task: Task, step: Step | None, action: str, status: str, reason: str | None,
            payload: dict, model: str | None) -> DirectorDecision:
    row = DirectorDecision(project_id=task.project_id, task_id=task.id, step_id=step.id if step else None,
                           action=action, status=status, reason=reason, payload=payload, model=model)
    db.add(row)
    return row


def _model_label(step: Step) -> str:
    from hub.services.scheduler import _model_label as label
    return label(step)


def _retry_or_hold(db: Session, task: Task, step: Step, now: datetime) -> None:
    other = router.alternate(step.model)
    tried = set(db.scalars(select(Step.model).where(Step.task_id == task.id, Step.seq == step.seq)))
    if step.error_class in router.SWITCHABLE and other and other not in tried:
        db.add(Step(task_id=task.id, seq=step.seq, kind=step.kind, model=other, attempt=step.attempt + 1,
                    input=step.input))
        return
    outcome = retry_decision(step.error_class, step.attempt, step.kind)
    if outcome is StepOutcome.WAIT:
        step.status, step.not_before, step.node_id = "pending", now + SESSION_LIMIT_WAIT, None
    elif outcome is StepOutcome.RETRY:
        db.add(Step(task_id=task.id, seq=step.seq, kind=step.kind, model=step.model, attempt=step.attempt + 1,
                    input=step.input))
    else:
        reason = f"디렉터 단계가 {step.attempt}회 실패했다({step.error_class}) — 결과 승인과 다음 작업을 직접 정해 주세요"
        _record(db, task, step, "hold", "pending", reason, {}, None)
        db.add(Event(task_id=task.id, step_id=step.id, level="error", message=reason))


def gate(db: Session, task: Task, project: Project, now: datetime, usage: dict[str, float | None]) -> None:
    """Called for a task at its result gate in a director project. Each tick does at most one thing."""
    latest = _latest_step(db, task)
    if latest is not None and latest.status in ("pending", "leased", "running"):
        return
    if pending_decision(db, task) is not None:
        return
    if latest is not None and latest.kind in ("charter", "direct"):
        decided = db.scalar(select(DirectorDecision.id).where(DirectorDecision.step_id == latest.id).limit(1))
        if decided is None and latest.status == "succeeded":
            _apply(db, task, project, latest, now)
            return
        if decided is None:
            _retry_or_hold(db, task, latest, now)
            return
    kind = "direct" if project.charter_confirmed_at else "charter"
    _new_gate_step(db, task, project, kind, (latest.seq if latest else 0) + 1, now, usage)


def _apply(db: Session, task: Task, project: Project, step: Step, now: datetime) -> None:
    """Apply a finished director step once. If applying fails, hold the gate instead of retrying every tick."""
    try:
        (_apply_charter if step.kind == "charter" else _apply_direct)(db, task, project, step, now)
    except Exception as exc:  # noqa: BLE001 - one bad result must not loop the scheduler
        db.rollback()
        log.exception("디렉터 판단 적용 실패 (작업 %s)", task.id)
        reason = f"디렉터 판단을 적용하지 못했다: {exc}"[:500] + " — 결과 승인과 다음 작업을 직접 정해 주세요"
        _record(db, task, step, "hold", "pending", reason, {}, None)
        db.add(Event(task_id=task.id, step_id=step.id, level="error", message=reason))
        db.commit()


def _apply_charter(db: Session, task: Task, project: Project, step: Step, now: datetime) -> None:
    out = step.output or {}
    project.charter = out["charter"]
    _record(db, task, step, "charter", "pending", out["summary"], {"journal": out.get("journal")},
            _model_label(step))
    db.add(Event(task_id=task.id, step_id=step.id, message="디렉터가 연구 헌장 초안을 냈다 — 연구자 확인 대기"))


def _updated_charter(charter: dict, out: dict) -> tuple[dict, bool]:
    budget = charter["budget"]
    base = {**out["charter"], "budget": budget} if out.get("charter") else charter
    changes = {u["id"]: u["status"] for u in out.get("milestones", [])}
    before = {m["id"]: m["status"] for m in charter["milestones"]}
    milestones = [{**m, "status": changes.get(m["id"], m["status"])} for m in base["milestones"]]
    # Progress means a milestone that was open is now settled; unknown ids or re-opening do not count.
    progress = any(before.get(i) == "open" and s in ("done", "dropped") for i, s in changes.items())
    return {**base, "milestones": milestones}, progress


def _apply_direct(db: Session, task: Task, project: Project, step: Step, now: datetime) -> None:
    out = step.output or {}
    model = _model_label(step)
    charter, progress = _updated_charter(project.charter, out)
    project.charter = charter
    payload = {"journal": out["journal"], "milestones": out.get("milestones", []), "progress": progress,
               "charter_why": out.get("charter_why")}
    if out["decision"] == "hold":
        _record(db, task, step, "hold", "pending", out["reason"], payload, model)
        db.add(Event(task_id=task.id, step_id=step.id, level="warn",
                     message=f"디렉터가 결과 승인을 보류했다: {out['reason'][:300]}"))
        return
    action = out["action"]
    options = (task.result_card or {}).get("next_options", [])
    if action == "continue" and not 0 <= out["next_option"] < len(options):
        _record(db, task, step, "hold", "pending", f"없는 후보 번호({out['next_option']})를 골랐다", payload, model)
        return
    if action in PROGRESS_ACTIONS:
        over = budget_usage(db, project, now)["over"]
        intended = {"action": action, "next_option": out.get("next_option"), "new_task": out.get("new_task")}
        if over:
            question = f"예산을 넘겼다: {', '.join(over)}. 디렉터가 하려던 다음 작업: {_intent_title(task, intended)}"
            _record(db, task, step, "budget", "pending", out["reason"],
                    {**payload, "over": over, "intended": intended, "question": question}, model)
            db.add(Event(task_id=task.id, step_id=step.id, level="warn", message=question[:500]))
            return
        _record(db, task, step, action, "applied", out["reason"], payload, model)
        _start_next(db, task, intended, now, f"agent:{model}")
        return
    if action == "ask":
        _record(db, task, step, "ask", "pending", out["reason"], {**payload, **out["ask"]}, model)
        db.add(Event(task_id=task.id, step_id=step.id, level="warn",
                     message=f"디렉터가 연구자에게 묻는다: {out['ask']['question'][:300]}"))
        return
    _record(db, task, step, "wrap_up", "pending", out["reason"], {**payload, "summary": out["wrap_up"]}, model)
    db.add(Event(task_id=task.id, step_id=step.id, message="디렉터가 프로젝트 마무리를 제안했다 — 종료 승인 대기"))


def _intent_title(task: Task, intended: dict) -> str:
    if intended["action"] == "new_task":
        return intended["new_task"]["title"]
    return (task.result_card or {})["next_options"][intended["next_option"]]["title"]


def _start_next(db: Session, task: Task, intended: dict, now: datetime, decided_by: str) -> Task | None:
    if intended["action"] == "new_task":
        return task_service.approve_result(db, task, None, now, decided_by=decided_by,
                                           follow_up=intended["new_task"])
    return task_service.approve_result(db, task, intended["next_option"], now, decided_by=decided_by)


def confirm_charter(db: Session, project: Project, charter: dict, now: datetime) -> None:
    project.charter = Charter.model_validate(charter).model_dump(mode="json")
    project.charter_confirmed_at = project.charter_confirmed_at or now
    for row in db.scalars(select(DirectorDecision).where(DirectorDecision.project_id == project.id,
                                                         DirectorDecision.action == "charter",
                                                         DirectorDecision.status == "pending")):
        row.status, row.answered_at, row.answer = "answered", now, {"text": "헌장 확정"}
    db.commit()


def answer(db: Session, row: DirectorDecision, choice: str, comment: str, now: datetime,
           extend: dict | None = None) -> None:
    """The researcher's reply to a pending decision. Most replies go back to the director on the next tick."""
    from hub.services import projects

    task = db.get(Task, row.task_id)
    if row.status != "pending" or task.status != "review":
        raise ValueError("답을 기다리는 디렉터 질문이 없습니다")
    text = choice + (f" — {comment}" if comment else "")
    row.status, row.answered_at, row.answer = "answered", now, {"choice": choice, "comment": comment, "text": text}
    project = db.get(Project, row.project_id)
    if row.action in ("charter", "hold"):
        raise ValueError("헌장 확인과 결과 보류는 각자의 화면에서 처리합니다")
    if row.action == "ask" and choice not in (row.payload or {}).get("options", []):
        raise ValueError("제시된 선택지 중에서 고르세요")
    if row.action == "budget" and choice == "extend":
        _extend_budget(project, extend or {})
        db.add(Event(task_id=task.id, message=f"연구자가 예산을 늘렸다 — 디렉터 계획대로 진행 ({comment or '메모 없음'})"))
        _start_next(db, task, row.payload["intended"], now, "human")
        return
    if row.action == "wrap_up" and choice == "close":
        projects.ensure_open(project)  # check before the result is approved, so a failure leaves nothing half-done
        task_service.approve_result(db, task, None, now)
        projects.close_project(db, project, now, (comment or (row.payload or {}).get("summary") or "")[:4000] or None)
        return
    db.add(Event(task_id=task.id, message=f"연구자 답: {text[:300]} — 디렉터가 다시 판단한다"))
    db.commit()


def _extend_budget(project: Project, extend: dict) -> None:
    charter = project.charter
    budget = dict(charter["budget"])
    if min(extend.get("tasks") or 0, extend.get("run_hours") or 0) < 0:
        raise ValueError("예산은 늘리는 값만 입력할 수 있습니다")
    if not (extend.get("tasks") or extend.get("run_hours") or extend.get("deadline")):
        raise ValueError("늘릴 작업 수·실행 시간·새 마감 중 하나를 입력하세요")
    if extend.get("tasks"):
        budget["max_tasks"] = budget["max_tasks"] + int(extend["tasks"])
    if extend.get("run_hours") and budget.get("max_run_hours"):
        budget["max_run_hours"] = budget["max_run_hours"] + float(extend["run_hours"])
    if extend.get("deadline"):
        budget["deadline"] = extend["deadline"]
    project.charter = {**charter, "budget": Charter.model_validate({**charter, "budget": budget}).model_dump(mode="json")["budget"]}


def kickoff(db: Session, project: Project) -> Task:
    """Hand an idle project to the director: one read-only task whose result gate drafts the charter."""
    if not is_active(project):
        raise ValueError("디렉터를 켜고 에이전트 승인을 허용한 프로젝트만 착수할 수 있습니다")
    if db.scalar(select(Task.id).where(Task.project_id == project.id, Task.status.notin_(TASK_FINISHED)).limit(1)):
        raise ValueError("이 프로젝트에는 이미 진행 중인 작업이 있습니다")
    return task_service.create_task(db, project, KICKOFF_TITLE, KICKOFF_OBJECTIVE,
                                    "핵심 결론·미해결 질문·남은 일이 근거 파일 위치와 함께 정리돼 있다")
