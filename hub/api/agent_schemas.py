from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Pct = Field(ge=0, le=100)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class Gpu(_Frozen):
    name: str = Field(max_length=128)
    util_pct: int | None = Field(default=None, ge=0, le=100)
    mem_used_mb: int | None = Field(default=None, ge=0)
    mem_total_mb: int | None = Field(default=None, ge=0)


class GpuProc(_Frozen):
    pid: int = Field(ge=0)
    name: str = Field(max_length=128)
    mem_mb: int | None = Field(default=None, ge=0)


class Metrics(_Frozen):
    load: list[float] = Field(default_factory=list, max_length=3)
    mem_total_mb: int | None = Field(default=None, ge=0)
    mem_used_mb: int | None = Field(default=None, ge=0)
    disk_total_gb: float | None = Field(default=None, ge=0)
    disk_used_gb: float | None = Field(default=None, ge=0)
    gpus: list[Gpu] = Field(default_factory=list, max_length=16)
    gpu_procs: list[GpuProc] = Field(default_factory=list, max_length=64)


class Heartbeat(_Frozen):
    agent_version: str = Field(max_length=32)
    hostname: str = Field(max_length=128)
    metrics: Metrics


class Login(_Frozen):
    logged_in: bool | None
    auth_method: str | None = Field(default=None, max_length=32)
    subscription: str | None = Field(default=None, max_length=32)
    error: str | None = Field(default=None, max_length=300)


class Logins(_Frozen):
    claude: Login
    codex: Login


class QuotaWindow(_Frozen):
    id: str = Field(max_length=64)
    label: str = Field(max_length=64)
    used_percent: float = Pct
    resets_at: str | None = Field(default=None, max_length=40)
    window_minutes: int | None = Field(default=None, ge=0)


class Quota(_Frozen):
    provider: Literal["claude", "codex"]
    windows: list[QuotaWindow] = Field(default_factory=list, max_length=16)
    error: str | None = Field(default=None, max_length=300)


class Progress(_Frozen):
    message: str | None = Field(default=None, max_length=500)


class Complete(_Frozen):
    status: Literal["succeeded", "failed"]
    result: dict | None = None
    error_class: str | None = Field(default=None, max_length=32)
