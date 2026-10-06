import json
import re

import pytest
from fastapi.testclient import TestClient

from hub.db.models import Event, HumanRequest, Node, Project, Step
from hub.main import create_app
from hub.services import human, leasing, scheduler, tasks
from hub.services.pipeline import validate_result
from tests.conftest import ADMIN_PASSWORD, NOW

FORM = {"instructions": "범주별 규칙을 정하세요.", "answers_path": "out/rule_answers.jsonl", "requires_human": True,
        "fields": [{"name": "winner", "label": "라벨 기준", "type": "choice", "choices": ["안의 물건", "바깥 용기"],
                    "required": True},
                   {"name": "rule_sentence", "label": "규칙 한 문장", "type": "text", "required": True},
                   {"name": "reviewer_id", "label": "검수자 이름(첫 항목만)", "type": "text", "required": "first"},
                   {"name": "memo", "label": "메모", "type": "text"}],
        "items": [{"id": "C01", "title": "신발"}, {"id": "C02", "title": "옷"}]}
PLAN = {"summary": "s", "approach": ["a"], "needs_implement": False,
        "run": {"command": "python build.py", "timeout_hours": 1}, "human_input": FORM}
DRAFT = {"summary": "2건 초안", "answers": {
    "C01": {"winner": "안의 물건", "rule_sentence": "신발만 보이면 other로 분류한다."},
    "C02": {"winner": "안의 물건", "rule_sentence": "옷만 보이면 clothing으로 분류한다."}}}
FULL = {"C01": {"winner": "안의 물건", "rule_sentence": "신발만 보이면 other로 분류한다.", "reviewer_id": "SHLee"},
        "C02": {"winner": "바깥 용기", "rule_sentence": "옷만 보이면 용기 라벨을 붙인다."}}


@pytest.fixture()
def world(db):
    node = Node(name="spark-2588", token_hash="b" * 64, labels=["claude", "codex"])
    project = Project(slug="drug", name="마약탐지", workdir="/w", node_selector=["node:spark-2588"],
                      auto_ai_review=False)  # this project's forms are answered by a person
    db.add_all([node, project])
    db.commit()
    task = tasks.create_task(db, project, "규칙 확정", "o", None)
    tasks.approve_start(db, task, NOW)
    scheduler.tick(db, NOW)
    plan = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, plan, node, "succeeded", PLAN, None, NOW)
    scheduler.tick(db, NOW)
    return node, task, db.query(HumanRequest).one()


def _draft(db, node, request, result=DRAFT):
    human.request_draft(db, request, NOW)
    scheduler.tick(db, NOW)
    review = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, review, node, "succeeded", result, None, NOW)
    scheduler.tick(db, NOW)
    return review


def test_form_schema_accepts_required_flags():
    form = validate_result("plan", PLAN)["human_input"]
    assert [f["required"] for f in form["fields"]] == [True, True, "first", False]
    with pytest.raises(ValueError):
        validate_result("plan", {**PLAN, "human_input": {**FORM, "fields": [
            {"name": "x", "label": "x", "type": "text", "required": "last"}]}})


def test_missing_required_lists_blank_fields():
    missing = human.missing_required(FORM, {"C01": {"winner": "안의 물건"}, "C02": {"winner": "안의 물건"}})
    assert missing == ["신발: 규칙 한 문장", "신발: 검수자 이름(첫 항목만)", "옷: 규칙 한 문장"]
    assert human.missing_required(FORM, FULL) == []


def test_submit_rejects_blank_required_but_keeps_input(db, world):
    _, task, request = world
    partial = {"C01": {"winner": "안의 물건"}, "C02": {"winner": "안의 물건"}}
    with pytest.raises(ValueError, match="규칙 한 문장"):
        human.submit(db, request, partial, NOW)
    db.refresh(request)
    db.refresh(task)
    assert request.status == "pending" and request.answers == partial and task.status == "waiting_human"
    human.save(db, request, partial, NOW)          # 중간 저장은 필수 칸과 무관
    human.submit(db, request, FULL, NOW)
    assert request.status == "submitted"


def test_web_submit_with_blank_required_is_422(settings, db, world):
    _, task, _ = world
    with TestClient(create_app(settings)) as client:
        client.post("/login", data={"password": ADMIN_PASSWORD})
        page = client.get(f"/tasks/{task.id}").text
        assert "규칙 한 문장 *" in page and "메모 *" not in page
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        res = client.post(f"/tasks/{task.id}/human-input",
                          data={"csrf": csrf, "action": "submit", "C01__winner": "안의 물건"}, follow_redirects=False)
        assert res.status_code == 422 and "규칙 한 문장" in res.text


def test_ai_draft_prefills_form_and_waits_for_person(db, world):
    node, task, request = world
    review = _draft(db, node, request)
    assert "초안" in review.input["prompt"]
    db.refresh(request)
    db.refresh(task)
    assert task.status == "waiting_human" and request.status == "pending" and request.answered_by == "human"
    assert request.answers == DRAFT["answers"]
    assert request.draft["by"] == review.model and request.draft["answers"] == DRAFT["answers"]
    assert any("AI 초안" in e.message for e in db.query(Event).all())


def test_draft_on_requires_human_form(db, world):
    _, task, request = world
    human.request_draft(db, request, NOW)
    db.refresh(task)
    assert task.status == "running" and request.answered_by == "draft"


def test_submit_after_draft_records_provenance(db, world):
    node, task, request = world
    review = _draft(db, node, request)
    human.submit(db, request, FULL, NOW)
    assert request.draft["changed_items"] == ["C01", "C02"]
    assert any("AI 초안 기반" in e.message and "수정 2개" in e.message for e in db.query(Event).all())
    scheduler.tick(db, NOW)
    run = leasing.lease_step(db, node, NOW)
    assert run.kind == "run"
    prov = json.loads(run.input["files"]["out/rule_answers.jsonl.provenance.json"])
    assert prov["draft_by"] == review.model and prov["submitted_by"] == "human"
    assert prov["changed_items"] == ["C01", "C02"] and prov["unchanged_items"] == []
    assert '"SHLee"' in run.input["files"]["out/rule_answers.jsonl"]


def test_provenance_names_the_person_when_there_was_no_draft(db, world):
    node, _, request = world
    human.submit(db, request, FULL, NOW)
    scheduler.tick(db, NOW)
    run = leasing.lease_step(db, node, NOW)
    assert list(run.input["files"]) == ["out/rule_answers.jsonl",
                                       "out/rule_answers.jsonl.provenance.json"]
    record = json.loads(run.input["files"]["out/rule_answers.jsonl.provenance.json"])
    assert record["submitted_by"] == "human"
    assert record["draft_by"] is None and record["answered_by_model"] is None


def test_final_ai_answers_must_fill_required(db, world):
    node, _, request = world
    request.form = {**request.form, "requires_human": False}
    db.commit()
    human.delegate_to_ai(db, request, NOW)
    scheduler.tick(db, NOW)
    review = leasing.lease_step(db, node, NOW)
    leasing.complete_step(db, review, node, "succeeded", DRAFT, None, NOW)
    scheduler.tick(db, NOW)
    assert db.get(Step, review.id).error_class == "bad_result"


def test_draft_button_on_task_page(settings, db, world):
    _, task, _ = world
    with TestClient(create_app(settings)) as client:
        client.post("/login", data={"password": ADMIN_PASSWORD})
        page = client.get(f"/tasks/{task.id}").text
        assert "AI 초안 채우기" in page
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        assert client.post(f"/tasks/{task.id}/draft-ai", data={"csrf": csrf},
                           follow_redirects=False).status_code == 303
    db.refresh(task)
    assert task.status == "running"


def test_second_draft_runs_a_new_review(db, world):
    node, task, request = world
    first = _draft(db, node, request)
    second = _draft(db, node, request, {"summary": "다시", "answers": {"C01": {"winner": "바깥 용기"}}})
    assert second.id != first.id and second.kind == "review"
    db.refresh(request)
    assert request.answers == {"C01": {"winner": "바깥 용기"}, "C02": {}} and request.draft["step_id"] == second.id
