from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentConfig:
    hub_url: str
    token: str
    disk_path: str = "/"
    execute: bool = True
    model: str = "opus"
    codex_bin: str | None = None
    codex_model: str | None = None
    codex_sandbox: str = "workspace-write"


def _parse_env(text: str) -> dict[str, str]:
    pairs = (line.split("=", 1) for line in text.splitlines() if "=" in line and not line.lstrip().startswith("#"))
    return {k.strip(): v.strip().strip('"') for k, v in pairs}


def load_config(path: Path) -> AgentConfig:
    values = _parse_env(path.read_text())
    for key in ("HUB_URL", "HUB_NODE_TOKEN"):
        if not values.get(key):
            raise ValueError(f"{path}: {key} 값이 필요합니다")
    return AgentConfig(values["HUB_URL"], values["HUB_NODE_TOKEN"], values.get("AGENT_DISK_PATH", "/"),
                       values.get("AGENT_EXECUTE", "1") not in ("0", "false", "no"),
                       values.get("AGENT_CLAUDE_MODEL") or values.get("AGENT_MODEL", "opus"),
                       values.get("AGENT_CODEX_BIN") or None, values.get("AGENT_CODEX_MODEL") or None,
                       values.get("AGENT_CODEX_SANDBOX", "workspace-write"))
