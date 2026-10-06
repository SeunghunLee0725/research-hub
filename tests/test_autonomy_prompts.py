"""에이전트에게 맡긴 연구는 사람만 통과하는 관문을 만들지 않는다 — 대신 누가 답했는지 남긴다."""
from hub.db.models import Project, Task
from hub.services.prompts import ROLE, build_prompt

PROJECT = Project(slug="p", name="프로젝트", workdir="/w", context="")
TASK = Task(title="t", objective="o", success_criteria="c")


def _prompt(kind):
    return build_prompt(kind, PROJECT, TASK, {}, "/w/result.json")


def test_forms_do_not_wait_for_a_person():
    prompt = _prompt("implement")
    assert "사람이 웹에서 답할 때까지 멈추고" not in prompt
    assert "에이전트가 채운다" in prompt


def test_no_gate_rejects_agent_answers():
    prompt = _prompt("implement")
    assert "submitted_by" in prompt and "거부하는 검사를 만들지 않는다" in prompt
    assert "AI 판정(약지도)" in prompt


def test_never_forge_a_person():
    assert "사람 이름" in _prompt("implement") and "위조" in _prompt("implement")


def test_diagnosis_proposes_what_the_agent_can_apply():
    role = ROLE["diagnose"]
    assert "연구자가 고른다" not in role
    assert "사람 제출을 기다리는" in role and "에이전트가 적용" in role


def test_approval_holds_only_when_the_check_contradicts_the_conclusion():
    role = ROLE["approve"]
    assert "의심스러우면 hold 쪽이다" not in role
    assert "정면으로" in role and "한계" in role
