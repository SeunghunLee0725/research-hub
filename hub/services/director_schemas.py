"""Result formats of the director's steps: the research charter and the decision at a task gate."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class Milestone(_Strict):
    id: str = Field(pattern=r"^M[0-9]{1,2}$")
    title: str = Field(min_length=1, max_length=200)
    done_when: str = Field(min_length=1, max_length=500)
    status: Literal["open", "done", "dropped"] = "open"


class Budget(_Strict):
    max_tasks: int = Field(ge=1, le=200)
    max_run_hours: float | None = Field(default=None, gt=0, le=10000)
    deadline: date | None = None


class Charter(_Strict):
    question: str = Field(min_length=1, max_length=1000)
    milestones: list[Milestone] = Field(min_length=1, max_length=12)
    deliverables: list[str] = Field(default_factory=list, max_length=10)
    boundaries: list[str] = Field(default_factory=list, max_length=12)
    budget: Budget

    @model_validator(mode="after")
    def _unique_ids(self):
        ids = [m.id for m in self.milestones]
        if len(ids) != len(set(ids)):
            raise ValueError("마일스톤 id 가 겹칩니다")
        return self


class CharterResult(_Strict):
    charter: Charter
    summary: str = Field(min_length=1, max_length=3000)
    journal: str = Field(default="", max_length=2000)


class NewTask(_Strict):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=6000)
    success_criteria: str | None = Field(default=None, max_length=3000)


class Ask(_Strict):
    question: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(min_length=1, max_length=4)


class MilestoneUpdate(_Strict):
    id: str = Field(pattern=r"^M[0-9]{1,2}$")
    status: Literal["open", "done", "dropped"]
    note: str = Field(default="", max_length=500)


class DirectResult(_Strict):
    decision: Literal["approved", "hold"]
    reason: str = Field(min_length=1, max_length=2000)
    action: Literal["continue", "new_task", "wrap_up", "ask"]
    next_option: int | None = Field(default=None, ge=0, le=9)
    new_task: NewTask | None = None
    ask: Ask | None = None
    wrap_up: str | None = Field(default=None, max_length=6000)
    milestones: list[MilestoneUpdate] = Field(default_factory=list, max_length=12)
    charter: Charter | None = None
    charter_why: str | None = Field(default=None, max_length=1000)
    journal: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _action_has_its_payload(self):
        if self.decision == "hold":
            return self
        missing = {"continue": self.next_option is None, "new_task": self.new_task is None,
                   "ask": self.ask is None, "wrap_up": not self.wrap_up}[self.action]
        if missing:
            field = {"continue": "next_option", "new_task": "new_task", "ask": "ask", "wrap_up": "wrap_up"}
            raise ValueError(f"action={self.action} 에는 {field[self.action]} 가 필요합니다")
        if self.charter is not None and not self.charter_why:
            raise ValueError("헌장을 바꾸면 charter_why 에 이유를 적어야 합니다")
        return self
