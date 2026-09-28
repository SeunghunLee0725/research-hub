from typing import Any


def ok(data: Any) -> dict:
    return {"success": True, "data": data, "error": None}


def fail(message: str) -> dict:
    return {"success": False, "data": None, "error": message}
