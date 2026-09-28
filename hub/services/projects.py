from sqlalchemy.orm import Session

from hub.db.models import Project


def create_project(db: Session, slug: str, name: str, workdir: str, node_selector: list[str]) -> Project:
    project = Project(slug=slug, name=name, workdir=workdir, node_selector=list(node_selector))
    db.add(project)
    db.commit()
    return project
