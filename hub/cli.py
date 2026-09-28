import argparse
import getpass
import re
import sys

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from datetime import datetime, timezone

from hub.config import Settings
from hub.db.models import Node, Project, Task
from hub.db.session import make_sessionmaker
from hub.security import hash_password
from hub.services import nodes, projects, tasks

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _labels(value: str) -> list[str]:
    return [label.strip() for label in value.split(",") if label.strip()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hub")
    sub = parser.add_subparsers(dest="command", required=True)
    add_node = sub.add_parser("add-node", help="노드를 등록하고 토큰을 한 번 출력")
    add_node.add_argument("name")
    add_node.add_argument("--labels", default="")
    rotate = sub.add_parser("rotate-token", help="노드 토큰 재발급")
    rotate.add_argument("name")
    add_project = sub.add_parser("add-project", help="프로젝트 등록")
    add_project.add_argument("slug")
    add_project.add_argument("name")
    add_project.add_argument("workdir")
    add_project.add_argument("--node-labels", default="")
    sub.add_parser("hash-password", help="관리자 비밀번호 해시 생성")
    add_task = sub.add_parser("add-task", help="작업 제안 등록(시작 승인 대기)")
    add_task.add_argument("slug")
    add_task.add_argument("title")
    add_task.add_argument("--objective", required=True)
    add_task.add_argument("--criteria")
    approve = sub.add_parser("approve-start", help="작업 시작 승인")
    approve.add_argument("task_id", type=int)
    return parser


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "hash-password":
        password = getpass.getpass("새 관리자 비밀번호: ")
        if len(password) < 8:
            print("비밀번호는 8자 이상이어야 합니다.", file=sys.stderr)
            return 2
        print(hash_password(password))
        return 0
    name = args.slug if args.command in ("add-project", "add-task") else getattr(args, "name", None)
    if name is not None and not NAME_RE.fullmatch(name):
        print(f"이름 형식이 잘못됐습니다(소문자·숫자·하이픈): {name}", file=sys.stderr)
        return 2
    settings = settings or Settings()
    with make_sessionmaker(settings.database_url)() as db:
        try:
            return _run(db, args)
        except IntegrityError:
            db.rollback()
            print(f"이미 존재합니다: {name}", file=sys.stderr)
            return 1


def _run(db, args) -> int:
    if args.command == "add-node":
        _, token = nodes.create_node(db, args.name, _labels(args.labels))
        print("노드 토큰(다시 표시되지 않습니다):")
        print(token)
        return 0
    if args.command == "rotate-token":
        node = db.scalar(select(Node).where(Node.name == args.name))
        if node is None:
            print(f"노드가 없습니다: {args.name}", file=sys.stderr)
            return 1
        print("새 노드 토큰(다시 표시되지 않습니다):")
        print(nodes.rotate_token(db, node))
        return 0
    if args.command == "add-task":
        return _add_task(db, args)
    if args.command == "approve-start":
        return _approve_start(db, args.task_id)
    projects.create_project(db, args.slug, args.name, args.workdir, _labels(args.node_labels))
    print(f"프로젝트 등록: {args.slug}")
    return 0


def _add_task(db, args) -> int:
    project = db.scalar(select(Project).where(Project.slug == args.slug))
    if project is None:
        print(f"프로젝트가 없습니다: {args.slug}", file=sys.stderr)
        return 1
    try:
        task = tasks.create_task(db, project, args.title, args.objective, args.criteria)
    except IntegrityError:
        db.rollback()
        print("이 프로젝트에는 이미 진행 중인 작업이 있습니다(프로젝트당 1개).", file=sys.stderr)
        return 1
    print(f"작업 #{task.id} 등록 — 시작 승인 대기")
    return 0


def _approve_start(db, task_id: int) -> int:
    task = db.get(Task, task_id)
    try:
        if task is None:
            raise ValueError(f"작업이 없습니다: {task_id}")
        tasks.approve_start(db, task, datetime.now(timezone.utc))
    except ValueError as exc:
        db.rollback()
        print(str(exc), file=sys.stderr)
        return 1
    print(f"작업 #{task_id} 시작 승인")
    return 0


if __name__ == "__main__":
    sys.exit(main())
