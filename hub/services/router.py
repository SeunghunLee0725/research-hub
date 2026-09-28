"""Chooses Claude or Codex for each step: role-based default, quota pressure, and error fallback."""
PREFERRED = {"plan": "claude", "implement": "codex", "run": "none",
             "analyze": "claude", "verify": "codex", "report": "claude"}
PRESSURE_PCT = 85.0
SWITCHABLE = {"session_limit", "auth", "refusal", "sandbox"}
_ALTERNATE = {"claude": "codex", "codex": "claude"}


def alternate(model: str) -> str | None:
    return _ALTERNATE.get(model)


def usage_from_windows(windows_by_provider: dict[str, list[dict]]) -> dict[str, float | None]:
    return {provider: max((w["used_percent"] for w in windows), default=None)
            for provider, windows in windows_by_provider.items()}


def _pressed(usage: dict[str, float | None], model: str) -> bool:
    value = usage.get(model)
    return value is not None and value >= PRESSURE_PCT


def choose_model(kind: str, usage: dict[str, float | None]) -> str:
    preferred = PREFERRED[kind]
    other = alternate(preferred)
    if other and _pressed(usage, preferred) and not _pressed(usage, other):
        return other
    return preferred
