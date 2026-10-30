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

import http.client
import json
import posixpath
import socket
import urllib.error
import urllib.request
from typing import Any, Callable, Optional
from urllib.parse import quote, unquote, urlencode

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
    def __init__(self, status: int, detail: str, payload: Optional[dict] = None):
        super().__init__(status, detail)
        self.status = status
        self.detail = detail
        self.payload = payload  # the API's whole detail dict, when it sent one

    def __str__(self) -> str:
        return f"HTTP {self.status}: {self.detail}"


def normalise_path(path: str) -> str:
    """The path as the app will resolve it: decoded, dot-segments and double
    slashes collapsed. Returns "" for a path that must not be sent."""
    decoded = path
    for _ in range(5):
        again = unquote(decoded)
        if again == decoded:
            break
        decoded = again
    else:
        return ""  # still changing after 5 rounds: not a real path
    if "\\" in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        return ""
    if not decoded.startswith("/"):
        return ""
    clean = posixpath.normpath(decoded)
    if clean.startswith("//"):  # normpath keeps a leading double slash
        clean = "/" + clean.lstrip("/")
    if clean == "/openapi.json" or clean.startswith("/api/"):
        return clean
    return ""


def reachable(path: str) -> bool:
    """Whether the server sends requests to this path at all."""
    clean = normalise_path(path)
    if clean == "/openapi.json":
        return True
    return bool(clean) and not clean.startswith(_REFUSED_PREFIXES)


def unreachable_sentence(path: str) -> str:
    return f"{path} is not available: the MCP server does not reach that route."


def _check_path(path: str) -> str:
    """The normalised path to send, or a sentence."""
    if not isinstance(path, str):
        raise ApiError(400, "The path must be a string like /api/customers.")
    if "?" in path or "#" in path:
        # Decoding a query along with the path would rewrite it (a%26b
        # became a&b); the query goes in params, where it is encoded whole.
        raise ApiError(
            400, "A path takes no query or fragment; put the query in params."
        )
    if not reachable(path):
        raise ApiError(403, unreachable_sentence(path))
    return normalise_path(path)


def _sentences(errors: list) -> str:
    """A 422's list of errors as one line of sentences: `field: message`."""
    out = []
    for err in errors:
        if not isinstance(err, dict):
            out.append(str(err))
            continue
        loc = [str(x) for x in err.get("loc", []) if x not in ("body", "query")]
        where = ".".join(loc)
        msg = err.get("message") or err.get("msg") or json.dumps(err)
        out.append(f"{where}: {msg}" if where else str(msg))
    return " ".join(out)


def _parse(raw: bytes):
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a 3xx surfaces as an HTTPError instead of being followed


def urllib_transport(config: Config) -> Transport:
    """The real transport: loopback HTTP with the token as a Bearer header.
    No proxies and no redirects, so the token only goes to base_url."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

    def send(method, path, params, body, headers):
        # The path arrives decoded (normalise_path); quote it again so a
        # space or a non-ASCII character is a valid URL, not an exception.
        url = config.base_url + quote(path)
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
            with opener.open(req, timeout=config.timeout) as resp:
                return resp.status, _parse(resp.read())
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise ApiError(
                    exc.code,
                    "SlowBooks Pro answered with a redirect, "
                    "which the MCP server does not follow.",
                ) from None
            return exc.code, _parse(exc.read())
        except (http.client.InvalidURL, UnicodeError, ValueError) as exc:
            # Refused before anything was sent, so the outcome is known.
            raise ApiError(400, f"The request could not be formed: {exc}") from None
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            socket.timeout,
            OSError,
        ) as exc:
            # Includes a half answer (IncompleteRead) and a non-HTTP answer
            # (BadStatusLine): the app died mid-reply or something else is
            # on the port. Status 0: the outcome is unknown.
            reason = getattr(exc, "reason", None) or exc
            raise ApiError(
                0,
                f"SlowBooks Pro did not answer at {config.base_url}: "
                f"{type(exc).__name__}: {reason}",
            ) from None

    return send


class ApiClient:
    def __init__(self, transport: Transport):
        self._transport = transport
        self.spec: Optional[dict] = None  # openapi.json, cached by the tools

    def _request(self, method, path, params=None, body=None, headers=None):
        path = _check_path(path)
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        status, payload = self._transport(method, path, clean, body, headers)
        if 200 <= status < 300:
            return payload
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        extra = None
        if isinstance(detail, dict) and isinstance(detail.get("message"), str):
            extra = detail
            detail = detail["message"]  # e.g. the possible-duplicate 409
        elif isinstance(detail, list):
            detail = _sentences(detail)  # a 422: one sentence per field
        elif detail and not isinstance(detail, str):
            detail = json.dumps(detail)
        raise ApiError(
            status, detail or f"the request failed with status {status}", extra
        )

    def get(self, path: str, params: Optional[dict] = None):
        return self._request("GET", path, params)

    def post(
        self,
        path: str,
        body: Any = None,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
    ):
        return self._request("POST", path, params, body, headers)
