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
    "approve": '{"decision": "approved"|"hold", "reason": str(한두 문장), "next_option": int|null}',
    "diagnose": '{"cause": str, "fix": str, "confidence": "high"|"medium"|"low", "risk": str, '
                '"retry_after_fix": bool}',
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
              "확인할 수 없으면 match=false 와 이유를 notes 에. 결과 파일 외에는 아무것도 수정하지 않는다. "
              "run 단계의 종료 코드·성공 표식·산출물 점검 결과와 로그 사본은 `.research-hub/steps/<run 단계 id>/run_result.json` 과 `run.log` 에 있다 — 정상 종료 여부는 여기서 확인한다.",
    "diagnose": "이 작업은 막혀 있다. 왜 막혔는지 원자료(실패한 단계의 로그·결과 파일·run_result.json·작업 "
                "디렉터리)에서 직접 확인하고, 무엇을 고쳐야 다시 돌아가는지 제안한다. 아무것도 고치지 말고 조사만 한다 "
                "— 제안을 적용할지는 연구자가 고른다. cause 는 로그에서 확인한 사실로 쓰고 추측이면 그렇게 밝힌다. "
                "fix 는 사람이 읽고 판단할 수 있게 구체적으로(어느 파일의 무엇을, 왜) 두세 문장으로. risk 에는 그 수정이 "
                "건드리는 범위와 되돌릴 수 있는지를 적는다. 근거가 약하면 confidence 를 low 로 한다.",
    "approve": "연구자를 대신해 결과를 승인할지 판단한다. 결과 카드의 결론·수치·한계와 검증(trust) 메모를 읽고, "
               "수치가 결론을 실제로 받치는지, 검증이 잡은 불일치가 결론을 흔드는지 본다. 받칠 때만 approved 를 고르고 "
               "다음 후보 중 이어서 할 것을 next_option 번호로 고른다(없으면 null). 근거가 부족하거나 검증이 통과하지 "
               "못했고 그 이유가 결론에 닿으면 hold 를 고른다 — 그러면 연구자가 직접 본다. 의심스러우면 hold 쪽이다. "
               "reason 에 판단 근거를 한두 문장으로 남긴다. 이 판단은 에이전트가 했다는 사실과 모델 이름이 기록된다.",
    "report": "사용자가 1분 안에 이해할 결과 카드를 쓴다. 전문 용어·내부 식별자 없이 한국어로. "
              "trust.verified 는 검증(verify) 단계가 verified=true 일 때만 true 로 쓰고, 불일치 항목은 trust.notes 에 옮긴다. "
              "한계는 숨기지 말 것. "
              "next_options 는 다음에 할 만한 작업 1~3개.",
}


def build_prompt(kind: str, project: Project, task: Task, outputs: dict[str, dict], result_path: str,
                 failures: list[dict] | None = None) -> str:
    history = json.dumps(outputs, ensure_ascii=False, indent=1)[:12000] if outputs else "(없음)"
    stuck = ("\n## 막힌 지점 (실패한 단계)\n" + json.dumps(failures, ensure_ascii=False, indent=1)[:12000]
             + "\n" if failures else "")
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
{stuck}
## 규칙
- 가장 먼저 작업 디렉터리의 plan.md(프로젝트 계획)를 읽고 그 방향을 따른다. 없으면 없다고 기록하고 진행한다.
  사용자가 올린 관련 자료는 materials/ 에 있다.
- 이 작업 하나에만 집중한다. 범위를 넓히지 않는다.
- 사람의 판단·라벨·확인이 필요한 부분은 직접 채우지 말고, plan/implement 결과의 human_input 에 폼을 정의한다.
  작업은 사람이 웹에서 답할 때까지 멈추고, 답은 answers_path 에 JSONL(한 줄에 {{"id": 항목 id, 필드명: 값}}, 답한 항목만)로
  저장된 뒤 다음 단계가 시작된다. run 명령은 그 파일을 읽도록 작성한다. 폼 형식:
  {{"instructions": str(2~3문장), "answers_path": 작업 디렉터리 기준 상대 경로, "layout": "cards"|"table",
  "fields": [{{"name": 소문자_이름, "label": str, "type": "choice"|"text", "choices": [str],
  "required": true|"first"|false}}],
  "items": [{{"id": 영숫자 id, "title": 짧은 제목, "body": 판단에 꼭 필요한 내용만, "priority": 1~9|null,
  "image": 작업 디렉터리 기준 이미지 상대 경로|null}}]}}
  사람이 읽는 양을 최소로 한다: 반복되는 안내·내부 식별자·AI 판정 전문은 body 에 넣지 않는다.
  이미지를 보고 판단하는 작업(예: X선 판독)은 layout="table" 로 하고 각 항목 image 에 썸네일(png/jpg, 5MB 이하) 경로를
  넣는다. 표에서는 body 가 접혀 보이므로 한두 줄로 충분하다. AI 판정은 사람의 판단을 끌고 가지 않도록 기본적으로 가린다.
  "requires_human": true 는 연구자 본인의 서명·승인이 필요한 폼에 표시한다. 표시해도 작업은 멈추지 않고
  에이전트가 자기 이름으로 채운다 — 그 사실과 모델 이름이 기록에 남는다. 폼을 읽는 쪽이 사람 서명과 구별할 수 있게
  하려는 표시일 뿐이다.
  run 명령이 반드시 읽는 필드는 "required": true(모든 항목) 또는 "first"(첫 항목만, 예: 검수자 이름)로 표시한다.
  빈 필수 칸이 있으면 웹에서 제출이 거부된다. 선택 입력(메모 등)은 생략하거나 false.
- 비밀값(.env, 토큰)을 출력하거나 파일에 복사하지 않는다.
- 되돌리기 어려운 조작(데이터 삭제, git push, 서비스 재시작)은 하지 않는다.
- 끝나면 반드시 아래 경로에 JSON 하나를 쓴다. 다른 형식은 실패로 처리된다.

결과 파일: {result_path}
형식: {SCHEMAS[kind]}
"""


DRAFT_RULES = """- 이것은 사람이 확인·수정한 뒤 제출할 **초안**이다. 모든 항목의 모든 필드(텍스트 포함)를 채운다.
- 폼 안내·항목 본문에 적힌 이전 답·예시·제약과 충돌하지 않게 고른다. 충돌이 불가피하면 summary 에 항목 id 와 이유를 적는다.
- 텍스트 필드는 사진·자료 없이도 다른 사람이 같은 판정을 내릴 수 있는 기준 문장으로 쓴다.
- 검수자 이름·서명처럼 사람 본인만 쓸 수 있는 필드는 비워 둔다."""


def build_review_prompt(project: Project, task: Task, form: dict, form_path: str, result_path: str,
                        draft: bool = False) -> str:
    fields = "; ".join(f"{f['name']}({f['type']}{': ' + '/'.join(f['choices']) if f['choices'] else ''})"
                       for f in form["fields"])
    background = f"\n## 프로젝트 배경 (항상 지킬 것)\n{project.context.strip()}\n" if project.context else ""
    role = "사람이 확인할 입력 초안 작성을" if draft else "사람 대신 판정을"
    extra = f"\n{DRAFT_RULES}" if draft else ""
    return f"""당신은 연구 프로젝트 "{project.name}"에서 {role} 맡았다. 작업 디렉터리: {project.workdir}
{background}
## 작업
제목: {task.title}
목표: {task.objective}

## 판정할 폼
폼 전체(안내·항목·필드)는 {form_path} 에 있다. 먼저 이 파일을 읽는다. 항목 수: {len(form['items'])}
필드: {fields}

## 규칙
- 작업 디렉터리의 plan.md(프로젝트 계획)를 먼저 읽는다. 관련 자료는 materials/ 에 있다.
- 항목마다 독립적으로, 폼 안내와 항목 본문의 기준대로 판단한다. 필요하면 작업 디렉터리의 원자료를 직접 확인한다.
- 본문에 이전 AI 판정이 있더라도 그대로 따르지 말고 근거를 스스로 확인한다. 애매하면 더 보수적인 선택지를 고르고 이유를 적는다.
- 텍스트 필드(이유·메모 등)는 한두 문장으로 판정 근거를 적는다.
- 폼이 연구자 본인의 서명·승인을 요구해도 칸을 비우지 않는다. **사람 이름을 적는 것은 기록 위조이므로 절대 하지 않는다.**
  이름·서명 칸에는 자신을 가리키는 식별자(예: agent:claude)를 적고, 판단 근거와 사람이 봤다면 달랐을 수 있는 점을
  메모 칸에 남긴다. 승인할 근거가 부족하면 '승인하지 않음' 쪽을 고르고 이유를 적는다.
- 파일을 수정하지 않는다. 결과 파일만 쓴다.{extra}

결과 파일: {result_path}
형식: {{"summary": str(판정 분포와 애매했던 항목 요약), "answers": {{항목 id: {{필드명: 값}}}}}}
"""
