import json
from pathlib import Path


def prepare(path: str) -> Path:
    """Remove any previous attempt's result so a stale file is never mistaken for this run's output."""
    result_path = Path(path)
    result_path.unlink(missing_ok=True)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    return result_path


def read_result(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
