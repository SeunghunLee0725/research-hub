from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentConfig:
    hub_url: str
    token: str
    disk_path: str = "/"


def _parse_env(text: str) -> dict[str, str]:
    pairs = (line.split("=", 1) for line in text.splitlines() if "=" in line and not line.lstrip().startswith("#"))
    return {k.strip(): v.strip().strip('"') for k, v in pairs}


def load_config(path: Path) -> AgentConfig:
    values = _parse_env(path.read_text())
    for key in ("HUB_URL", "HUB_NODE_TOKEN"):
        if not values.get(key):
            raise ValueError(f"{path}: {key} 값이 필요합니다")
    return AgentConfig(values["HUB_URL"], values["HUB_NODE_TOKEN"], values.get("AGENT_DISK_PATH", "/"))
