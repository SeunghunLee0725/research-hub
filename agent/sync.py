"""Keeps project folders on this node in shape: creates workdirs and writes files dropped on the web page."""
import logging
import time
from pathlib import Path

from agent.client import HubError

log = logging.getLogger("research-hub-agent")


def resolve_workdir(workdir: str) -> Path:
    return Path(workdir).expanduser().resolve()


def _safe_target(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if Path(relative).is_absolute() or ".." in Path(relative).parts or root not in target.parents:
        raise ValueError(f"작업 폴더 밖 경로: {relative}")
    return target


def _write(root: Path, relative: str, data: bytes) -> Path:
    target = _safe_target(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and relative == "plan.md":
        target.rename(target.with_name(f"plan.md.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
    elif target.exists():
        stem, suffix, n = target.stem, target.suffix, 2
        while target.exists():
            target = target.with_name(f"{stem} ({n}){suffix}")
            n += 1
    tmp = target.with_name(target.name + ".part")
    tmp.write_bytes(data)
    tmp.rename(target)
    return target


class Syncer:
    def __init__(self, client):
        self._client = client
        self._status: dict[str, dict] = {}

    def _ensure(self, projects: list[dict]) -> dict[int, Path]:
        roots = {}
        for project in projects:
            try:
                root = resolve_workdir(project["workdir"])
                (root / "materials").mkdir(parents=True, exist_ok=True)
                roots[project["id"]] = root
                self._status[str(project["id"])] = {"ok": True, "error": None}
            except OSError as exc:
                self._status[str(project["id"])] = {"ok": False, "error": str(exc)[:300]}
        return roots

    def run(self) -> None:
        try:
            data = self._client.post("/agent/v1/sync", {"projects": self._status})
        except HubError as exc:
            log.warning("sync 실패: %s", exc)
            return
        roots = self._ensure(data.get("projects", []))
        for upload in data.get("uploads", []):
            self._deliver(upload, roots.get(upload["project_id"]))

    def _deliver(self, upload: dict, root: Path | None) -> None:
        path = f"/agent/v1/uploads/{upload['id']}/result"
        try:
            if root is None:
                raise ValueError("작업 폴더를 만들 수 없음")
            _safe_target(root, upload["path"])
            data = self._client.get_bytes(f"/agent/v1/uploads/{upload['id']}/content")
            written = _write(root, upload["path"], data)
            self._client.post(path, {"ok": True, "written_path": str(written), "error": None})
        except (OSError, ValueError, HubError) as exc:
            log.warning("upload %s 실패: %s", upload["id"], exc)
            try:
                self._client.post(path, {"ok": False, "written_path": None, "error": str(exc)[:500]})
            except HubError:
                pass
