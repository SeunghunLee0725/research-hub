"""Prompts for model steps. Every step must end by writing a JSON result file."""
import json

from hub.db.models import Project, Task

SCHEMAS = {
    "plan": '{"summary": str, "approach": [str], "needs_implement": bool, '
            '"run": {"command": str, "timeout_hours": float, "expected_artifacts": [str], '
            '"success_marker": str|null} | null, "human_input": <사람 입력 폼> | null}',
    "implement": '{"summary": str, "changed_files": [str], "run": <plan과 같은 run 형식> | null, '
                 '"human_input": <사람 입력 폼> | null}',
    "analyze": '{"summary": str, "findings": [str], "criteria_met": bool|null}',
    "verify": '{"verified": bool, "checks": [{"claim": str, "recomputed": str, "match": bool}], "notes": [str]}',
    "report": '{"result_card": {"conclusion": str(한 문장, 300자 이내), "what_we_did": str(쉬운 말 2~3문장), '
              '"metrics": [{"name": str, "baseline": str|null, "result": str, "note": str}], '
              '"trust": {"verified": bool, "notes": [str]}, "limits": [str], '
              '"next_options": [{"title": str, "why": str, "est_hours": float}] (1~3개)}}',
}

ROLE = {
    "plan": "이 작업의 실행 계획을 세운다. 필요한 파일을 읽고 무엇을 어떻게 할지 정한다. "
            "코드 수정이 필요하면 needs_implement=true. 계산·평가를 돌려야 하면 run.command 에 "
            "작업 디렉터리에서 실행할 셸 명령 하나를 적는다(오래 걸려도 된다. 직접 실행하지 말 것).",
    "implement": "계획에 따라 코드를 수정하고 빠른 테스트로 확인한다. 긴 계산은 직접 돌리지 말고 run.command 로 넘긴다.",
    "analyze": "실행 결과(로그·산출물)를 원자료에서 직접 확인해 해석한다. 수치는 파일에서 읽은 값만 쓴다. "
               "성공 기준 충족 여부를 판단한다.",
    "verify": "독립 검증자다. 앞 단계(분석)가 주장한 핵심 수치·사실마다, 분석 글을 믿지 말고 원자료(로그·결과 파일·데이터)에서 "
              "직접 다시 계산하거나 읽어 확인한다. 항목마다 claim(주장), recomputed(내가 얻은 값), match 를 적는다. "
              "확인할 수 없으면 match=false 와 이유를 notes 에. 결과 파일 외에는 아무것도 수정하지 않는다.",
    "report": "사용자가 1분 안에 이해할 결과 카드를 쓴다. 전문 용어·내부 식별자 없이 한국어로. "
              "trust.verified 는 검증(verify) 단계가 verified=true 일 때만 true 로 쓰고, 불일치 항목은 trust.notes 에 옮긴다. "
              "한계는 숨기지 말 것. "
              "next_options 는 다음에 할 만한 작업 1~3개.",
}


def build_prompt(kind: str, project: Project, task: Task, outputs: dict[str, dict], result_path: str) -> str:
    history = json.dumps(outputs, ensure_ascii=False, indent=1)[:12000] if outputs else "(없음)"
    background = f"\n## 프로젝트 배경 (항상 지킬 것)\n{project.context.strip()}\n" if project.context else ""
    return f"""당신은 연구 프로젝트 "{project.name}"의 {kind} 단계를 맡았다. 작업 디렉터리: {project.workdir}
{background}
## 작업
제목: {task.title}
목표: {task.objective}
성공 기준: {task.success_criteria or '(명시 없음 — 계획 단계에서 측정 가능한 기준을 제안할 것)'}

## 이번 단계의 역할
{ROLE[kind]}

## 앞 단계 결과
{history}

## 규칙
- 이 작업 하나에만 집중한다. 범위를 넓히지 않는다.
- 사람의 판단·라벨·확인이 필요한 부분은 직접 채우지 말고, plan/implement 결과의 human_input 에 폼을 정의한다.
  작업은 사람이 웹에서 답할 때까지 멈추고, 답은 answers_path 에 JSONL(한 줄에 {{"id": 항목 id, 필드명: 값}}, 답한 항목만)로
  저장된 뒤 다음 단계가 시작된다. run 명령은 그 파일을 읽도록 작성한다. 폼 형식:
  {{"instructions": str(2~3문장), "answers_path": 작업 디렉터리 기준 상대 경로, "layout": "cards"|"table",
  "fields": [{{"name": 소문자_이름, "label": str, "type": "choice"|"text", "choices": [str]}}],
  "items": [{{"id": 영숫자 id, "title": 짧은 제목, "body": 판단에 꼭 필요한 내용만, "priority": 1~9|null,
  "image": 작업 디렉터리 기준 이미지 상대 경로|null}}]}}
  사람이 읽는 양을 최소로 한다: 반복되는 안내·내부 식별자·AI 판정 전문은 body 에 넣지 않는다.
  이미지를 보고 판단하는 작업(예: X선 판독)은 layout="table" 로 하고 각 항목 image 에 썸네일(png/jpg, 5MB 이하) 경로를
  넣는다. 표에서는 body 가 접혀 보이므로 한두 줄로 충분하다. AI 판정은 사람의 판단을 끌고 가지 않도록 기본적으로 가린다.
- 비밀값(.env, 토큰)을 출력하거나 파일에 복사하지 않는다.
- 되돌리기 어려운 조작(데이터 삭제, git push, 서비스 재시작)은 하지 않는다.
- 끝나면 반드시 아래 경로에 JSON 하나를 쓴다. 다른 형식은 실패로 처리된다.

결과 파일: {result_path}
형식: {SCHEMAS[kind]}
"""
