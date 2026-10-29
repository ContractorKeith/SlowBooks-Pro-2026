# ============================================================================
# The HTTP client: every tool call is one request to the running app under
# the token. Standard library only, so this module needs no SDK. The
# transport is a plain callable, which is how the tests run the tools
# against the app in-process.
#
# The path guard is defence in depth: the middleware already refuses
# /api/tokens, /api/users and backup restore to any token. Refusing here too
# gives the agent a clear sentence instead of a bare 403.
# ============================================================================

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Callable, Optional
from urllib.parse import urlencode

from app.mcp.config import Config

# (method, path, params, body, headers) -> (status, parsed JSON or text)
Transport = Callable[[str, str, Optional[dict], Any, Optional[dict]], tuple]

_REFUSED_PREFIXES = (
    "/api/tokens",
    "/api/users",
    "/api/backups",
    "/api/company-file",
    "/api/system",
)


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(status, detail)
        self.status = status
        self.detail = detail

    def __str__(self) -> str:
        return f"HTTP {self.status}: {self.detail}"


def _check_path(path: str) -> None:
    if path == "/openapi.json":
        return
    if not path.startswith("/api/") or path.startswith(_REFUSED_PREFIXES):
        raise ApiError(
            403,
            f"{path} is not available: the MCP server does not reach that route.",
        )


def _parse(raw: bytes):
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def urllib_transport(config: Config) -> Transport:
    """The real transport: loopback HTTP with the token as a Bearer header."""

    def send(method, path, params, body, headers):
        url = config.base_url + path
        if params:
            url += "?" + urlencode(params, doseq=True)
        hdrs = {
            "Authorization": f"Bearer {config.token}",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=config.timeout) as resp:
                return resp.status, _parse(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _parse(exc.read())
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise ApiError(
                0,
                f"SlowBooks Pro did not answer at {config.base_url}: {reason}",
            ) from None

    return send


class ApiClient:
    def __init__(self, transport: Transport):
        self._transport = transport
        self.spec: Optional[dict] = None  # openapi.json, cached by the tools

    def _request(self, method, path, params=None, body=None, headers=None):
        _check_path(path)
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        status, payload = self._transport(method, path, clean, body, headers)
        if 200 <= status < 300:
            return payload
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        if isinstance(detail, dict) and isinstance(detail.get("message"), str):
            detail = detail["message"]  # e.g. the possible-duplicate 409
        elif detail and not isinstance(detail, str):
            detail = json.dumps(detail)  # a validation error is a list
        raise ApiError(status, detail or f"the request failed with status {status}")

    def get(self, path: str, params: Optional[dict] = None):
        return self._request("GET", path, params)

    def post(self, path: str, body: Any = None, headers: Optional[dict] = None):
        return self._request("POST", path, None, body, headers)
