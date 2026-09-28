import re

from fastapi import Request

VIEW_KEY = "view"
_PHONE = re.compile(r"Mobi|Android|iPhone", re.I)


def remember_choice(request: Request) -> None:
    choice = request.query_params.get("view")
    if choice in ("mobile", "desktop"):
        request.session[VIEW_KEY] = choice


def is_mobile(request: Request) -> bool:
    choice = request.session.get(VIEW_KEY)
    if choice in ("mobile", "desktop"):
        return choice == "mobile"
    return bool(_PHONE.search(request.headers.get("user-agent", "")))
