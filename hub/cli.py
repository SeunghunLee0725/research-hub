import argparse
import getpass
import re
import sys

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from hub.config import Settings
from hub.db.models import Node
from hub.db.session import make_sessionmaker
from hub.security import hash_password
from hub.services import nodes, projects

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
    name = getattr(args, "name", None) if args.command != "add-project" else args.slug
    if not NAME_RE.fullmatch(name):
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
    projects.create_project(db, args.slug, args.name, args.workdir, _labels(args.node_labels))
    print(f"프로젝트 등록: {args.slug}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
