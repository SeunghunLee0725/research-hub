import json
import urllib.error
import urllib.request


class HubError(RuntimeError):
    pass


class HubClient:
    def __init__(self, base_url: str, token: str, opener=urllib.request.urlopen, timeout: int = 15):
        self._base, self._token, self._open, self._timeout = base_url.rstrip("/"), token, opener, timeout

    def post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self._base + path, data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"})
        try:
            with self._open(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise HubError(f"HTTP {exc.code} {path}") from exc
        except (OSError, ValueError) as exc:
            raise HubError(f"{path}: {exc}") from exc
        if not body.get("success"):
            raise HubError(f"{path}: {body.get('error')}")
        return body.get("data")

    def get_bytes(self, path: str) -> bytes:
        req = urllib.request.Request(self._base + path, method="GET",
                                     headers={"Authorization": f"Bearer {self._token}"})
        try:
            with self._open(req, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise HubError(f"HTTP {exc.code} {path}") from exc
        except OSError as exc:
            raise HubError(f"{path}: {exc}") from exc
