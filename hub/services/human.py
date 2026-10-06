"""Requests for work only a person can do, answered through the web form."""
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.db.models import Event, HumanRequest, Step, Task


def create_request(db: Session, task: Task, step: Step, form: dict, by_llm: bool = False) -> HumanRequest:
    request = HumanRequest(task_id=task.id, step_id=step.id, form=form, answers_path=form["answers_path"],
                           answered_by="llm" if by_llm else "human")
    db.add(request)
    if by_llm:
        db.add(Event(task_id=task.id, step_id=step.id,
                     message=f"판정 {len(form['items'])}건 — 프로젝트 설정에 따라 LLM이 대신 판정"))
    else:
        task.status = "waiting_human"
        db.add(Event(task_id=task.id, step_id=step.id, message=f"사람 작업 필요 — {len(form['items'])}개 항목 입력 대기"))
    db.flush()
    return request


def reviews_by_ai(project, task, form: dict) -> bool:
    """Every form goes to the agent, signatures included: it signs under its own model name so the
    record shows who decided. Only the researcher asking for a task keeps a person in the loop."""
    if task.review_by == "human":
        return False
    return bool(project.auto_ai_review)


def delegate_to_ai(db: Session, request: HumanRequest, now: datetime) -> None:
    """Hands this pending form to the agent, including one marked requires_human."""
    if request.status != "pending":
        raise ValueError("이미 제출된 입력입니다")
    task = db.get(Task, request.task_id)
    request.answered_by, request.answers, request.draft = "llm", {}, None
    request.answered_by_model = None
    task.status = "running"
    db.add(Event(task_id=task.id, message="사용자가 판정을 AI에게 맡김"))
    db.commit()


def request_draft(db: Session, request: HumanRequest, now: datetime) -> None:
    """The LLM fills a draft into this form; a person still reviews and submits (allowed on requires_human forms)."""
    if request.status != "pending":
        raise ValueError("이미 제출된 입력입니다")
    task = db.get(Task, request.task_id)
    request.answered_by = "draft"
    task.status = "running"
    db.add(Event(task_id=task.id, message="사용자가 AI 초안을 요청함 — 초안이 채워지면 다시 사람 확인 대기"))
    db.commit()


def accept_draft(db: Session, request: HumanRequest, answers: dict[str, dict], step_id: int, model: str,
                 summary: str | None, now: datetime) -> None:
    task = db.get(Task, request.task_id)
    request.answers, request.answered_by = answers, "human"
    request.draft = {"by": model, "at": now.isoformat(), "summary": (summary or "")[:1000], "answers": answers,
                     "step_id": step_id}
    request.answered_by_model = None
    task.status = "waiting_human"
    filled = sum(1 for v in answers.values() if v)
    db.add(Event(task_id=task.id, message=f"AI 초안 채움({model}, {filled}/{len(request.form['items'])}개 항목) — 확인 후 제출"))


def reopen(db: Session, request: HumanRequest, now: datetime) -> None:
    """Ask the person again (e.g. the run rejected an AI proxy answer); the failed step reruns on submit.
    A form still pending (its AI review kept failing) is the one to hand over, never an earlier answered one."""
    task = db.get(Task, request.task_id)
    if task.status != "problem" or request.status not in ("submitted", "pending"):
        raise ValueError("다시 입력할 수 있는 상태가 아닙니다")
    request.status, request.answered_by, request.answers, request.submitted_at = "pending", "human", {}, None
    request.draft, request.answered_by_model = None, None
    task.status = "waiting_human"
    db.add(Event(task_id=task.id, message="사용자가 입력 폼을 다시 열었음 — 사람 입력 대기"))
    db.commit()


def last_submitted(db: Session, task: Task) -> HumanRequest | None:
    return db.scalar(select(HumanRequest).where(HumanRequest.task_id == task.id, HumanRequest.status == "submitted")
                     .order_by(HumanRequest.id.desc()).limit(1))


def validate_answers(form: dict, answers: dict[str, dict], partial: bool = False) -> dict[str, dict]:
    """Check answers produced by a model against the form; same rules as the web form (drafts may be partial)."""
    items = {item["id"] for item in form["items"]}
    fields = {f["name"]: f for f in form["fields"]}
    unknown = set(answers) - items
    if unknown:
        raise ValueError(f"폼에 없는 항목: {sorted(unknown)[:3]}")
    data = {}
    for item_id, values in answers.items():
        extra = set(values) - set(fields)
        if extra:
            raise ValueError(f"{item_id}: 폼에 없는 필드 {sorted(extra)}")
        data.update({f"{item_id}__{name}": str(value) for name, value in values.items()})
    parsed = parse_answers(form, data)
    missing = [] if partial else missing_required(form, parsed)
    if missing:
        raise ValueError(f"필수 입력 누락: {', '.join(missing[:5])}")
    return parsed


def missing_required(form: dict, answers: dict[str, dict]) -> list[str]:
    """Blank required fields as '<item title>: <field label>' (required: true = every item, "first" = first item)."""
    missing = []
    for index, item in enumerate(form["items"]):
        for field in form["fields"]:
            need = field.get("required") is True or (field.get("required") == "first" and index == 0)
            if need and not (answers.get(item["id"]) or {}).get(field["name"]):
                missing.append(f"{item['title']}: {field['label']}")
    return missing


def accept_ai_answers(db: Session, request: HumanRequest, answers: dict[str, dict], now: datetime,
                      model: str | None = None) -> None:
    request.answers, request.status, request.submitted_at = answers, "submitted", now
    request.answered_by_model = model
    filled = sum(1 for v in answers.values() if v)
    db.add(Event(task_id=request.task_id,
                 message=f"에이전트 판정 완료 ({filled}/{len(request.form['items'])}개 항목, {model or '모델 미기록'})"))


def open_request(db: Session, task: Task) -> HumanRequest | None:
    return db.scalar(select(HumanRequest).where(HumanRequest.task_id == task.id, HumanRequest.status == "pending")
                     .order_by(HumanRequest.id.desc()).limit(1))


def request_for_step(db: Session, step: Step) -> HumanRequest | None:
    return db.scalar(select(HumanRequest).where(HumanRequest.step_id == step.id))


def parse_answers(form: dict, data: dict[str, str]) -> dict[str, dict]:
    """Form posts use `<item id>__<field name>`; unknown keys are ignored, invalid choices rejected."""
    fields = {f["name"]: f for f in form["fields"]}
    answers: dict[str, dict] = {}
    for item in form["items"]:
        values = {}
        for name, spec in fields.items():
            value = (data.get(f"{item['id']}__{name}") or "").strip()
            if not value:
                continue
            if spec["type"] == "choice" and value not in spec["choices"]:
                raise ValueError(f"{item['id']}: {spec['label']} 값이 올바르지 않습니다")
            values[name] = value[:2000]
        answers[item["id"]] = values
    return answers


def answers_jsonl(form: dict, answers: dict[str, dict]) -> str:
    lines = [json.dumps({"id": item["id"], **answers[item["id"]]}, ensure_ascii=False)
             for item in form["items"] if answers.get(item["id"])]
    return "".join(line + "\n" for line in lines)


def save(db: Session, request: HumanRequest, answers: dict[str, dict], now: datetime) -> None:
    if request.status != "pending":
        raise ValueError("이미 제출된 입력입니다")
    request.answers = answers
    db.commit()


def submit(db: Session, request: HumanRequest, answers: dict[str, dict], now: datetime) -> None:
    """Answers are saved first so a rejected submit (blank required field) loses nothing."""
    save(db, request, answers, now)
    missing = missing_required(request.form, answers)
    if missing:
        raise ValueError(f"필수 입력 누락 — 입력한 내용은 저장했습니다: {', '.join(missing[:5])}"
                         + (f" 외 {len(missing) - 5}개" if len(missing) > 5 else ""))
    task = db.get(Task, request.task_id)
    filled = sum(1 for v in answers.values() if v)
    request.status, request.submitted_at = "submitted", now
    # A form may have been handed to the agent, but this submission came from a person: say so,
    # or the provenance would name a model that did not produce these answers.
    request.answered_by, request.answered_by_model = "human", None
    task.status = "running"
    note = ""
    if request.draft:
        changed = [item["id"] for item in request.form["items"]
                   if answers.get(item["id"], {}) != request.draft["answers"].get(item["id"], {})]
        request.draft = {**request.draft, "changed_items": changed}
        note = f", AI 초안 기반 · 사람 수정 {len(changed)}개 항목"
    db.add(Event(task_id=task.id, message=f"사람 입력 제출 ({filled}/{len(request.form['items'])}개 항목{note}) — 진행 재개"))
    db.commit()


def submitted_files(db: Session, task: Task) -> dict[str, str]:
    requests = db.scalars(select(HumanRequest).where(HumanRequest.task_id == task.id,
                                                     HumanRequest.status == "submitted")
                          .order_by(HumanRequest.id)).all()
    files = {}
    for r in requests:
        files[r.answers_path] = answers_jsonl(r.form, r.answers)
        # Always: a reader needs to know who produced these answers, person or agent.
        files[f"{r.answers_path}.provenance.json"] = provenance_json(r)
    return files


def provenance_json(request: HumanRequest) -> str:
    """Who produced these answers: a person, a person over an AI draft, or the agent (with its model)."""
    draft = request.draft or {}
    changed = draft.get("changed_items", [])
    record = {"answers_path": request.answers_path, "submitted_by": request.answered_by,
              "answered_by_model": request.answered_by_model,
              "submitted_at": request.submitted_at.isoformat() if request.submitted_at else None,
              "draft_by": draft.get("by"), "draft_at": draft.get("at")}
    if draft:
        record["changed_items"] = changed
        record["unchanged_items"] = [i["id"] for i in request.form["items"] if i["id"] not in changed]
    return json.dumps(record, ensure_ascii=False, indent=1) + "\n"
