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


def delegate_to_ai(db: Session, request: HumanRequest, now: datetime) -> None:
    if request.status != "pending":
        raise ValueError("이미 제출된 입력입니다")
    task = db.get(Task, request.task_id)
    request.answered_by, request.answers = "llm", {}
    task.status = "running"
    db.add(Event(task_id=task.id, message="사용자가 판정을 AI에게 맡김"))
    db.commit()


def validate_answers(form: dict, answers: dict[str, dict]) -> dict[str, dict]:
    """Check answers produced by a model against the form; same rules as the web form."""
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
    return parse_answers(form, data)


def accept_ai_answers(db: Session, request: HumanRequest, answers: dict[str, dict], now: datetime) -> None:
    request.answers, request.status, request.submitted_at = answers, "submitted", now
    filled = sum(1 for v in answers.values() if v)
    db.add(Event(task_id=request.task_id, message=f"LLM 판정 완료 ({filled}/{len(request.form['items'])}개 항목)"))


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
    save(db, request, answers, now)
    task = db.get(Task, request.task_id)
    filled = sum(1 for v in answers.values() if v)
    request.status, request.submitted_at = "submitted", now
    task.status = "running"
    db.add(Event(task_id=task.id, message=f"사람 입력 제출 ({filled}/{len(request.form['items'])}개 항목) — 진행 재개"))
    db.commit()


def submitted_files(db: Session, task: Task) -> dict[str, str]:
    requests = db.scalars(select(HumanRequest).where(HumanRequest.task_id == task.id,
                                                     HumanRequest.status == "submitted")
                          .order_by(HumanRequest.id)).all()
    return {r.answers_path: answers_jsonl(r.form, r.answers) for r in requests}
