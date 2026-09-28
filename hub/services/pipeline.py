"""Pure rules for how a task moves through its steps. No I/O here."""
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

RETRY_LIMIT = 3
RETRYABLE = {"timeout", "no_result", "bad_result", "lost", "tool_error", "exit_nonzero"}
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


class PlanResult(_Strict):
    summary: str = Field(min_length=1)
    approach: list[str]
    needs_implement: bool
    run: RunSpec | None = None


class ImplementResult(_Strict):
    summary: str = Field(min_length=1)
    changed_files: list[str] = Field(default_factory=list)
    run: RunSpec | None = None


class RunResult(_Strict):
    exit_code: int
    duration_s: float = Field(ge=0)
    log_tail: str = ""


class AnalyzeResult(_Strict):
    summary: str = Field(min_length=1)
    findings: list[str]
    criteria_met: bool | None = None


class Check(_Strict):
    claim: str = Field(min_length=1, max_length=300)
    recomputed: str = Field(max_length=300)
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


RESULT_MODELS = {"plan": PlanResult, "implement": ImplementResult, "run": RunResult,
                 "analyze": AnalyzeResult, "verify": VerifyResult, "report": ReportResult}


def validate_result(kind: str, result: dict | None) -> dict:
    try:
        return RESULT_MODELS[kind].model_validate(result or {}).model_dump()
    except ValidationError as exc:
        raise ValueError(f"{kind} 결과 형식 오류: {exc.error_count()}개 항목") from exc


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


def retry_decision(error_class: str | None, attempt: int) -> StepOutcome:
    if error_class in WAITABLE:
        return StepOutcome.WAIT
    if error_class in RETRYABLE and attempt < RETRY_LIMIT:
        return StepOutcome.RETRY
    return StepOutcome.PROBLEM
