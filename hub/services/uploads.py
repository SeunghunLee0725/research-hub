"""Files dropped on a project page. The hub holds them until the project's node writes them into the workdir."""
import hashlib
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.db.models import Node, Project, Upload

MAX_BYTES = 50 * 1024 * 1024
_SAFE = re.compile(r"[^\w .()\[\]+,@=-]", re.UNICODE)


def target_path(filename: str) -> str:
    """Where a dropped file lands, relative to the workdir: plan.md at the top, everything else in materials/."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = _SAFE.sub("_", name)[:180]
    if not name or name.startswith("."):
        raise ValueError(f"사용할 수 없는 파일 이름: {filename!r}")
    return "plan.md" if name.lower() == "plan.md" else f"materials/{name}"


def add_upload(db: Session, project: Project, filename: str, data: bytes) -> Upload:
    if len(data) > MAX_BYTES:
        raise ValueError(f"{filename}: 파일이 너무 큽니다(최대 {MAX_BYTES // 1024 // 1024}MB)")
    upload = Upload(project_id=project.id, path=target_path(filename), size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(), data=data)
    db.add(upload)
    db.commit()
    return upload


def node_projects(db: Session, node: Node) -> list[Project]:
    """Projects pinned to this node (their workdir lives there)."""
    return [p for p in db.scalars(select(Project).order_by(Project.id)) if f"node:{node.name}" in p.node_selector]


def pending_for(db: Session, projects: list[Project]) -> list[Upload]:
    ids = [p.id for p in projects]
    if not ids:
        return []
    return list(db.scalars(select(Upload).where(Upload.status == "pending", Upload.project_id.in_(ids))
                           .order_by(Upload.id).limit(20)))


def record_result(db: Session, upload: Upload, ok: bool, written_path: str | None, error: str | None,
                  now: datetime) -> None:
    upload.status = "delivered" if ok else "failed"
    upload.written_path, upload.error, upload.delivered_at = written_path, error, now
    if ok:
        upload.data = None  # the node has it now; do not keep a second copy
    db.commit()
