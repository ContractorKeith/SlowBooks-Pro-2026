# ============================================================================
# The tools, as plain functions over an ApiClient: the SDK wrapper lives in
# server.py. Each returns JSON-able data kept compact (no PDF, CSV or HTML).
# ============================================================================

import difflib
from typing import Optional

from app.mcp.client import ApiClient, ApiError

# kind -> list route. The first eight are the books' main records.
KINDS = {
    "customers": "/api/customers",
    "vendors": "/api/vendors",
    "items": "/api/items",
    "accounts": "/api/accounts",
    "invoices": "/api/invoices",
    "bills": "/api/bills",
    "expenses": "/api/expenses",
    "journal_entries": "/api/journal",
    # Statement lines (feeds and imports), the review queue.
    "bank_transactions": "/api/banking/transactions",
}
# Routes that take their own `limit`, so the page is cut server-side too.
_SERVER_LIMIT = {"invoices", "bills", "bank_transactions"}


def _kind_path(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"Unknown kind {kind!r}. Kinds: {', '.join(KINDS)}.")
    return KINDS[kind]


def _spec(client: ApiClient) -> dict:
    if client.spec is None:
        client.spec = client.get("/openapi.json")
    return client.spec


def context(client: ApiClient) -> dict:
    """Company and app facts to read before working."""
    out: dict = {}
    status = client.get("/api/auth/status")
    out["company"] = status.get("company_name") or ""
    if status.get("accountants_copy"):
        out["accountants_copy"] = status["accountants_copy"]
    settings = client.get("/api/settings")
    out["company_type"] = settings.get("company_type") or ""
    out["closing_date"] = settings.get("closing_date") or None
    entity = settings.get("entity")
    if entity:
        out["entity"] = entity
    # The spec's version is the app's; /health and /api/system are not
    # reachable from here. No route reports the token's own role, so the key
    # is left out: the API enforces it and answers a refusal with a sentence.
    out["app_version"] = (_spec(client).get("info") or {}).get("version", "")
    try:
        out["companies"] = [
            c.get("name") for c in client.get("/api/companies") if isinstance(c, dict)
        ]
    except ApiError:
        pass
    return out


def _resolve(client: ApiClient, node, depth=1):
    """Follow a $ref one level so a request body reads without a second call."""
    if isinstance(node, dict) and "$ref" in node and depth >= 0:
        target = _spec(client)
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target.get(part, {})
        return _resolve(client, target, depth - 1)
    if isinstance(node, dict):
        return {k: _resolve(client, v, depth) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(client, v, depth) for v in node]
    return node


def _item(client: ApiClient, op: dict) -> dict:
    out = {"summary": op.get("summary", "")}
    if op.get("parameters"):
        out["parameters"] = _resolve(client, op["parameters"])
    body = (op.get("requestBody") or {}).get("content", {}).get("application/json")
    if body:
        out["request_body"] = _resolve(client, body.get("schema", {}))
    return out


def api_schema(client: ApiClient, path: str):
    """How to call a route: methods, parameters and request body."""
    paths = _spec(client).get("paths", {})
    if path in paths:
        return {
            method: _item(client, op)
            for method, op in paths[path].items()
            if isinstance(op, dict)
        }
    rows = [
        {
            "path": p,
            "methods": sorted(m for m in item if m in _HTTP),
            "summary": next(
                (o.get("summary", "") for o in item.values() if isinstance(o, dict)),
                "",
            ),
        }
        for p, item in paths.items()
        if p.startswith(path)
    ]
    if rows:
        return rows[:20]
    return f"No route matches {path!r}. Try a shorter prefix such as /api/customers."


_HTTP = {"get", "post", "put", "patch", "delete"}


def search(client: ApiClient, q: str, limit: int = 20) -> dict:
    """Find records by name, number or amount across the books."""
    found = client.get("/api/search", {"q": q})
    out, left = {}, max(1, limit)
    for group, rows in found.items():
        if rows and left > 0:
            out[group] = rows[:left]
            left -= len(out[group])
    return out


def list_records(
    client: ApiClient, kind: str, filters: Optional[dict] = None, limit: int = 50
):
    """List one kind of record; filters pass through as query parameters."""
    path = _kind_path(kind)
    params = dict(filters or {})
    if kind in _SERVER_LIMIT:
        params.setdefault("limit", limit)
    rows = client.get(path, params)
    return rows[:limit] if isinstance(rows, list) else rows


def get_record(client: ApiClient, kind: str, record_id: int):
    """One record by id."""
    path = _kind_path(kind)
    if kind == "bank_transactions":
        raise ValueError(
            "A statement line has no single-record route; use list_records "
            "with filters like {'bank_account_id': 1}."
        )
    return client.get(f"{path}/{int(record_id)}")


def _reports(client: ApiClient) -> dict:
    """name -> parameters, for the plain GET reports (no PDF, CSV or id)."""
    out = {}
    for path, item in _spec(client).get("paths", {}).items():
        op = item.get("get")
        if not path.startswith("/api/reports/") or op is None or "{" in path:
            continue
        if path.endswith(("/pdf", "/csv")):
            continue
        out[path[len("/api/reports/") :]] = [
            {
                "name": p["name"],
                "required": bool(p.get("required")),
                "type": (p.get("schema") or {}).get("type", "string"),
            }
            for p in op.get("parameters", [])
            if p.get("in") == "query"
        ]
    return out


def list_reports(client: ApiClient) -> list:
    """The reports that can be run, with their parameters."""
    return [{"name": n, "params": p} for n, p in sorted(_reports(client).items())]


def report(client: ApiClient, name: str, params: Optional[dict] = None):
    """Run a report by name (see list_reports)."""
    known = _reports(client)
    if name not in known:
        close = difflib.get_close_matches(name, known, n=5, cutoff=0.4)
        hint = f" Closest: {', '.join(close)}." if close else ""
        raise ValueError(f"No report named {name!r}.{hint} Use list_reports.")
    return client.get(f"/api/reports/{name}", params)


def api_get(client: ApiClient, path: str, params: Optional[dict] = None):
    """Read-only GET of any other API route (see api_schema)."""
    return client.get(path, params)
