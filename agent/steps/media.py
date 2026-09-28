"""Uploads images referenced by a human-input form so the hub can show them to the reviewer."""
import base64
from pathlib import Path

CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                 ".webp": "image/webp"}
MAX_BYTES = 5 * 1024 * 1024
MAX_FILES = 300


def form_images(workdir: str, result: dict | None) -> list[dict]:
    """Return upload payloads for existing, allowed image files inside the workdir; skip everything else."""
    items = ((result or {}).get("human_input") or {}).get("items") or []
    root = Path(workdir).resolve()
    uploads, seen = [], set()
    for item in items[:MAX_FILES]:
        relative = item.get("image") if isinstance(item, dict) else None
        if not relative or relative in seen:
            continue
        seen.add(relative)
        target = (root / relative).resolve()
        content_type = CONTENT_TYPES.get(target.suffix.lower())
        if root not in target.parents or content_type is None or not target.is_file():
            continue
        if target.stat().st_size > MAX_BYTES:
            continue
        uploads.append({"path": relative, "content_type": content_type,
                        "data_b64": base64.b64encode(target.read_bytes()).decode()})
    return uploads
