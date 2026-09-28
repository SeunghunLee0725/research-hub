"""Pure rules for how a task moves through its steps. No I/O here."""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

RETRY_LIMIT = 3
RETRYABLE = {"timeout", "no_result", "bad_result", "lost", "tool_error"}
# A shell command that exits non-zero or times out will almost always do so again; only a lost node is retried.
RETRYABLE_RUN = {"lost"}
WAITABLE = {"session_limit"}


class StepOutcome(Enum):
    RETRY = "retry"
    WAIT = "wait"
    PROBLEM = "problem"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class RunSpec(_Strict):
    command: str = Field(min_length=1, max_length=4000)
    timeout_hours: float = Field(gt=0, le=72)
    expected_artifacts: list[str] = Field(default_factory=list, max_length=20)
    success_marker: str | None = Field(default=None, max_length=200)


class FormField(_Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=100)
    type: Literal["choice", "text"]
    choices: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _choices(self):
        if self.type == "choice" and not self.choices:
            raise ValueError("choice 필드에는 choices 가 필요합니다")
        return self


def is_relative_inside(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return bool(path) and not path.startswith("/") and ".." not in parts


class FormItem(_Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=8000)
    priority: int | None = Field(default=None, ge=1, le=9)
    image: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _image_path(self):
        if self.image is not None and not is_relative_inside(self.image):
            raise ValueError("image 는 작업 디렉터리 안의 상대 경로여야 합니다")
        return self


class HumanInput(_Strict):
    instructions: str = Field(min_length=1, max_length=3000)
    answers_path: str = Field(min_length=1, max_length=300)
    layout: Literal["cards", "table"] = "cards"
    fields: list[FormField] = Field(min_length=1, max_length=6)
    items: list[FormItem] = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def _relative_path(self):
        if not is_relative_inside(self.answers_path):
            raise ValueError("answers_path 는 작업 디렉터리 안의 상대 경로여야 합니다")
        return self


class PlanResult(_Strict):
    summary: str = Field(min_length=1)
    approach: list[str]
    needs_implement: bool
    run: RunSpec | None = None
    human_input: HumanInput | None = None


class ImplementResult(_Strict):
    summary: str = Field(min_length=1)
    changed_files: list[str] = Field(default_factory=list)
    run: RunSpec | None = None
    human_input: HumanInput | None = None


class RunResult(_Strict):
    exit_code: int
    duration_s: float = Field(ge=0)
    log_tail: str = ""


class AnalyzeResult(_Strict):
    summary: str = Field(min_length=1)
    findings: list[str]
    criteria_met: bool | None = None


class ReviewResult(_Strict):
    summary: str = Field(min_length=1, max_length=2000)
    answers: dict[str, dict[str, str]]


class Check(_Strict):
    claim: str = Field(min_length=1, max_length=2000)
    recomputed: str = Field(max_length=2000)
    match: bool


class VerifyResult(_Strict):
    verified: bool
    checks: list[Check] = Field(default_factory=list, max_length=30)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self):
        # "verified" only holds if at least one claim was recomputed and every check matched.
        consistent = self.verified and bool(self.checks) and all(c.match for c in self.checks)
        return self if consistent == self.verified else self.model_copy(update={"verified": consistent})


class Metric(_Strict):
    name: str
    baseline: str | None = None
    result: str
    note: str = ""


class Trust(_Strict):
    verified: bool
    notes: list[str] = Field(default_factory=list)


class NextOption(_Strict):
    title: str = Field(min_length=1, max_length=200)
    why: str = Field(max_length=500)
    est_hours: float = Field(ge=0, le=72)


class ResultCard(_Strict):
    conclusion: str = Field(min_length=1, max_length=300)
    what_we_did: str = Field(min_length=1, max_length=600)
    metrics: list[Metric] = Field(default_factory=list, max_length=12)
    trust: Trust
    limits: list[str] = Field(default_factory=list, max_length=8)
    next_options: list[NextOption] = Field(min_length=1, max_length=3)


class ReportResult(_Strict):
    result_card: ResultCard


RESULT_MODELS = {"plan": PlanResult, "implement": ImplementResult, "run": RunResult, "review": ReviewResult,
                 "analyze": AnalyzeResult, "verify": VerifyResult, "report": ReportResult}


def validate_result(kind: str, result: dict | None) -> dict:
    try:
        return RESULT_MODELS[kind].model_validate(result or {}).model_dump()
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "(최상위)"
        raise ValueError(f"{kind} 결과 형식 오류: {where} — {first['msg']} (총 {exc.error_count()}개)") from exc


def run_spec(kind: str, result: dict, outputs: dict[str, dict]) -> dict | None:
    if kind == "implement":
        return result.get("run") or (outputs.get("plan") or {}).get("run")
    return result.get("run")


def next_kind(kind: str, result: dict, outputs: dict[str, dict]) -> str | None:
    """outputs: validated results of earlier succeeded steps, keyed by kind."""
    if kind == "plan":
        if result.get("needs_implement"):
            return "implement"
        return "run" if result.get("run") else "analyze"
    if kind == "implement":
        return "run" if run_spec(kind, result, outputs) else "analyze"
    return {"run": "analyze", "analyze": "verify", "verify": "report", "report": None}[kind]


def enforce_trust(card: dict, verify: dict | None) -> dict:
    """The report may not claim more trust than the independent verify step established."""
    notes = list(card.get("trust", {}).get("notes", []))
    if verify is None:
        return {**card, "trust": {"verified": False, "notes": [*notes, "독립 검증 단계 결과 없음"]}}
    mismatches = [f"불일치: {c['claim']} → 재계산 {c['recomputed']}" for c in verify["checks"] if not c["match"]]
    extra = [n for n in mismatches if n not in notes]
    if not verify["checks"]:
        extra.append("검증 단계가 다시 계산한 수치가 없음")
    return {**card, "trust": {"verified": bool(verify["verified"]), "notes": [*notes, *extra]}}


def retry_decision(error_class: str | None, attempt: int, kind: str = "plan") -> StepOutcome:
    if error_class in WAITABLE:
        return StepOutcome.WAIT
    retryable = RETRYABLE_RUN if kind == "run" else RETRYABLE
    if error_class in retryable and attempt < RETRY_LIMIT:
        return StepOutcome.RETRY
    return StepOutcome.PROBLEM
