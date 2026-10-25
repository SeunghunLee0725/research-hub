"""Prompts for the project director: draft the research charter, then decide the direction at every task gate."""
import json

from hub.db.models import Project, Task

DIRECTION_PATH = ".research-hub/direction.md"
STALL_AFTER = 5  # gates in a row without milestone progress before the director must step back

CHARTER_SCHEMA = (
    '{"charter": {"question": str(이 프로젝트가 답할 최종 질문 한두 문장), '
    '"milestones": [{"id": "M1".., "title": str, "done_when": str(측정 가능한 완료 조건), '
    '"status": "open"|"done"|"dropped"}] (3~8개), "deliverables": [str], "boundaries": [str], '
    '"budget": {"max_tasks": int(헌장 확정 뒤 쓸 작업 수 상한), "max_run_hours": float|null(머신 실행 시간 상한), '
    '"deadline": "YYYY-MM-DD"|null}}, "summary": str(지금까지의 성과와 남은 일, 연구자가 1분 안에 읽을 분량), '
    '"journal": str(연구 일지에 남길 한두 문장)}')
DIRECT_SCHEMA = (
    '{"decision": "approved"|"hold", "reason": str, "action": "continue"|"new_task"|"wrap_up"|"ask", '
    '"next_option": int|null, "new_task": {"title": str, "objective": str, "success_criteria": str}|null, '
    '"ask": {"question": str, "options": [str](2~4개)}|null, "wrap_up": str|null(종합 보고), '
    '"milestones": [{"id": "M1", "status": "open"|"done"|"dropped", "note": str}], '
    '"charter": <헌장 전체, 바꿀 때만>|null, "charter_why": str|null, "journal": str}')

CHARTER_ROLE = """당신은 이 연구 프로젝트의 디렉터(PI 대리)로 새로 임명됐다. 지금까지는 연구자가 작업 결과를 보고 다음 방향을
정했다. 앞으로는 당신이 프로젝트 전체를 보고 방향을 정한다. 먼저 연구자가 확인할 **연구 헌장 초안**을 쓴다.
- plan.md, materials/, 연구노트·결과 파일, 아래 작업 이력을 읽고 이 프로젝트가 결국 답하려는 질문을 한두 문장으로 정한다.
- 마일스톤은 그 질문에 이르는 3~8개의 큰 단계다. 작업 하나 크기가 아니라 결론 하나 크기로 잡는다.
  done_when 은 수치·산출물로 판정할 수 있게 쓴다. 이미 끝난 것은 status=done 으로 둔다.
- deliverables 는 프로젝트가 끝날 때 남아야 할 것(보고서, 모델, 데이터셋, 그림 등).
- boundaries 는 하지 말아야 할 것. 프로젝트 배경 지시에 있는 금지·범위 제한을 빠짐없이 옮긴다.
- budget.max_tasks 는 남은 마일스톤을 끝내는 데 필요한 작업 수에 여유를 조금 더한 값. deadline 은 프로젝트 종료일이
  있으면 그 날짜, 없으면 null.
- 아무것도 실행하거나 수정하지 않는다. 읽기만 하고 결과 파일을 쓴다."""

DIRECT_ROLE = f"""당신은 이 연구 프로젝트의 디렉터(PI 대리)다. 연구자를 대신해 방금 끝난 작업의 결과를 받아들일지 정하고,
프로젝트 전체의 다음 방향을 정한다. 기준은 작업 하나가 아니라 **헌장의 최종 질문과 마일스톤**이다.
1. 결과 승인(decision): 결과 카드의 수치가 결론을 받치는지, 검증(trust)이 잡은 불일치가 결론을 흔드는지 본다.
   기본은 approved. hold 는 검증이 결론과 정면으로 어긋나는데 카드가 그 결론을 그대로 주장할 때만.
2. 마일스톤(milestones): 이 결과로 상태가 바뀐 마일스톤만 적는다(done/dropped/open). 근거를 note 에.
3. 다음 방향(action):
   - continue: 결과 카드의 다음 후보(next_option 번호)가 남은 마일스톤에 가장 가까울 때.
   - new_task: 후보가 마일스톤에서 벗어나거나, 다른 접근이 더 빠를 때. 목표·성공 기준을 직접 쓴다.
     성공 기준은 측정 가능하게, 한 작업(몇 시간~하루) 크기로.
   - wrap_up: 열린 마일스톤이 없거나, 남은 마일스톤이 이 프로젝트 범위에서 더 나아갈 수 없을 때. wrap_up 에 종합 보고
     (최종 질문에 대한 답, 마일스톤별 결과, 산출물 위치, 한계, 후속 연구 제안)를 쓴다. 종료는 연구자가 승인한다.
   - ask: **새 결과가 이전의 핵심 결론(완료된 마일스톤의 근거)과 정면으로 충돌할 때만**. 무엇이 충돌하는지와
     택할 수 있는 길 2~4개를 쓴다. 그 밖의 판단은 묻지 말고 스스로 정한다.
4. 헛돌지 않는다: 같은 하위 질문을 점검·재점검하는 작업을 이어 가지 않는다. 마일스톤 진척 없이 작업이
   {STALL_AFTER}개 이상 이어졌다면, 계속하려면 왜 다음 작업이 진척을 낼지 reason 에 구체적으로 쓰고, 아니면 접근을 바꾸거나
   마일스톤을 dropped 로 정리하고 다음으로 넘어간다.
5. 헌장(charter)은 연구 방향이 실제로 바뀔 때만 전체를 다시 써서 낸다(charter_why 필수). 예산은 바꿀 수 없다 — 허브가 관리한다.
6. journal 에는 이번 판단을 연구 일지 한 줄로 남긴다(무엇을 보고 무엇을 정했나).
아무것도 실행하거나 수정하지 않는다. 필요하면 작업 디렉터리의 원자료를 읽고, 결과 파일만 쓴다."""


def _history(tasks: list[Task]) -> str:
    lines = []
    for t in tasks:
        conclusion = ((t.result_card or {}).get("conclusion") or "").replace("\n", " ")[:200]
        lines.append(f"- #{t.id} [{t.status}] {t.title}" + (f" → {conclusion}" if conclusion else ""))
    return "\n".join(lines) or "(없음)"


def _common(project: Project, task: Task, history: list[Task]) -> str:
    background = f"\n## 프로젝트 배경 (항상 지킬 것)\n{project.context.strip()}\n" if project.context else ""
    period = f"{project.starts_on or '?'} ~ {project.ends_on or '?'}"
    card = json.dumps(task.result_card or {}, ensure_ascii=False, indent=1)[:8000]
    return f"""연구 프로젝트 "{project.name}" · 작업 디렉터리: {project.workdir} · 기간 {period}
{background}
## 작업 이력 (오래된 것부터, 최근 40개)
{_history(history)}

## 방금 끝난 작업 #{task.id} "{task.title}"
목표: {task.objective[:3000]}
성공 기준: {task.success_criteria or '(명시 없음)'}
결과 카드:
{card}
"""


def _rules(result_path: str, schema: str) -> str:
    return f"""
## 규칙
- 가장 먼저 plan.md 와 {DIRECTION_PATH}(있으면)를 읽는다. 관련 자료는 materials/ 에 있다.
- 수치는 결과 카드·결과 파일에서 확인한 값만 쓴다. 추측은 추측이라고 밝힌다.
- 비밀값(.env, 토큰)을 출력하거나 복사하지 않는다. 되돌리기 어려운 조작은 하지 않는다.
- 끝나면 반드시 아래 경로에 JSON 하나를 쓴다. 다른 형식은 실패로 처리된다.

결과 파일: {result_path}
형식: {schema}
"""


def build_charter_prompt(project: Project, task: Task, history: list[Task], result_path: str) -> str:
    return (f"{CHARTER_ROLE}\n\n{_common(project, task, history)}"
            f"{_rules(result_path, CHARTER_SCHEMA)}")


def build_direct_prompt(project: Project, task: Task, history: list[Task], result_path: str, *,
                        budget: dict, stall: int, answers: list[dict], journal: list[str]) -> str:
    charter = json.dumps(project.charter, ensure_ascii=False, indent=1)
    stalled = (f"\n**주의: 마일스톤 진척 없이 작업이 {stall}개 이어졌다. 4번 규칙을 적용한다.**\n"
               if stall >= STALL_AFTER else "")
    replies = "".join(f"\n- {a['asked']}\n  → 연구자 답: {a['answer']}" for a in answers)
    replies = f"\n## 연구자 답변 (이 작업에서 물은 것, 반드시 따른다){replies}\n" if replies else ""
    log = "\n".join(f"- {line}" for line in journal) or "(없음)"
    return f"""{DIRECT_ROLE}

## 연구 헌장 (연구자 확인)
{charter}

## 예산 사용
작업 {budget['tasks_used']}/{budget['max_tasks']}개 · 머신 실행 {budget['run_hours']:.1f}시간\
{f"/{budget['max_run_hours']}시간" if budget['max_run_hours'] else ''} · 마감 {budget['deadline'] or '없음'}
마일스톤 진척 없이 이어진 작업: {stall}개
{stalled}
## 최근 연구 일지
{log}
{replies}
{_common(project, task, history)}{_rules(result_path, DIRECT_SCHEMA)}"""
