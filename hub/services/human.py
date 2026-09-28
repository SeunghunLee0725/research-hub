"""Requests for work only a person can do, answered through the web form."""
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.db.models import Event, HumanRequest, Step, Task


def create_request(db: Session, task: Task, step: Step, form: dict) -> HumanRequest:
    request = HumanRequest(task_id=task.id, step_id=step.id, form=form, answers_path=form["answers_path"])
    db.add(request)
    task.status = "waiting_human"
    db.add(Event(task_id=task.id, step_id=step.id, message=f"사람 작업 필요 — {len(form['items'])}개 항목 입력 대기"))
    return request


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
