import pytest
from sqlalchemy.exc import IntegrityError

from hub.db.models import Approval, Node, Project, Task


def _project(db, slug="plasma-kg"):
    project = Project(slug=slug, name="Plasma KG", workdir="/srv/plasma")
    db.add(project)
    db.commit()
    return project


def test_one_active_task_per_project(db):
    project = _project(db)
    db.add(Task(project_id=project.id, title="first", objective="o", status="running"))
    db.commit()
    db.add(Task(project_id=project.id, title="second", objective="o", status="proposed"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_new_task_allowed_after_previous_finishes(db):
    project = _project(db)
    db.add(Task(project_id=project.id, title="first", objective="o", status="done"))
    db.add(Task(project_id=project.id, title="old", objective="o", status="cancelled"))
    db.add(Task(project_id=project.id, title="second", objective="o", status="proposed"))
    db.commit()


def test_active_tasks_in_different_projects_are_independent(db):
    a, b = _project(db, "a"), _project(db, "b")
    db.add_all([
        Task(project_id=a.id, title="t", objective="o", status="running"),
        Task(project_id=b.id, title="t", objective="o", status="running"),
    ])
    db.commit()


def test_task_status_is_constrained(db):
    project = _project(db)
    db.add(Task(project_id=project.id, title="t", objective="o", status="bogus"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_only_one_pending_approval_per_task_and_kind(db):
    project = _project(db)
    task = Task(project_id=project.id, title="t", objective="o", status="proposed")
    db.add(task)
    db.commit()
    db.add(Approval(task_id=task.id, kind="start", status="pending"))
    db.commit()
    db.add(Approval(task_id=task.id, kind="start", status="pending"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_node_names_are_unique(db):
    db.add(Node(name="spark-a", token_hash="1" * 64))
    db.commit()
    db.add(Node(name="spark-a", token_hash="2" * 64))
    with pytest.raises(IntegrityError):
        db.commit()
