"""The local MCP server (#655): config, the loopback client, the read tools.

The tools run against the app in-process: a transport over a cookie-less
TestClient with a real bookkeeper ApiToken, so the Bearer path through the
middleware is what every tool exercises."""

import io
import json
import os
import subprocess
import sys

import pytest

from app.mcp import tools
from app.mcp.client import ApiClient, ApiError
from app.mcp.config import ConfigError, load_config
from app.mcp.server import build_server

# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

SECRET = "sbp_" + "a" * 40


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A valid environment; tests change one thing at a time."""
    token_file = tmp_path / "token"
    token_file.write_text(SECRET + "\n")
    token_file.chmod(0o600)
    monkeypatch.delenv("SLOWBOOKS_URL", raising=False)
    monkeypatch.delenv("SLOWBOOKS_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("SLOWBOOKS_TOKEN_FILE", str(token_file))
    return token_file


def test_config_defaults(env):
    cfg = load_config()
    assert cfg.base_url == "http://127.0.0.1:3001"
    assert cfg.token == SECRET
    assert cfg.timeout == 120
    assert SECRET not in repr(cfg)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:3001",
        "http://192.168.1.20:3001",
        "http://127.0.0.1:3001/api",
        "http://user:pw@127.0.0.1:3001",
        "http://127.0.0.1:3001?x=1",
        "not a url",
    ],
)
def test_config_refuses_non_loopback_urls(env, monkeypatch, url):
    monkeypatch.setenv("SLOWBOOKS_URL", url)
    with pytest.raises(ConfigError, match="loopback http://"):
        load_config()


def test_config_accepts_loopback_names(env, monkeypatch):
    for url in ("http://localhost:3001/", "http://[::1]:3001"):
        monkeypatch.setenv("SLOWBOOKS_URL", url)
        assert load_config().base_url == url.rstrip("/")


def test_config_token_file_required_and_present(env, monkeypatch, tmp_path):
    monkeypatch.delenv("SLOWBOOKS_TOKEN_FILE")
    with pytest.raises(ConfigError, match="SLOWBOOKS_TOKEN_FILE"):
        load_config()
    monkeypatch.setenv("SLOWBOOKS_TOKEN_FILE", str(tmp_path / "missing"))
    with pytest.raises(ConfigError, match="does not exist"):
        load_config()


def test_config_token_file_permissions(env):
    env.chmod(0o640)
    if os.name == "nt":
        # Every file reads as 0o666 on Windows, so the check is skipped there.
        assert load_config().token == SECRET
    else:
        with pytest.raises(ConfigError, match="chmod 600"):
            load_config()


@pytest.mark.parametrize(
    "content", ["", "\n", "hello\n", SECRET + "\nsecond line\n", "xsbp_abc\n"]
)
def test_config_token_content_refused_without_echo(env, content):
    env.write_text(content)
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert "sbp_" in str(exc.value)
    for line in content.split():
        if len(line) > 5:  # the content, not a word of the sentence
            assert line not in str(exc.value)


def test_config_error_never_contains_token(env):
    env.write_text(SECRET + "\nmore\n")
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert SECRET not in str(exc.value)


@pytest.mark.parametrize("value", ["0", "0.5", "3601", "abc", "nan", "inf", ""])
def test_config_timeout_bounds(env, monkeypatch, value):
    monkeypatch.setenv("SLOWBOOKS_TIMEOUT_SECONDS", value)
    if value == "":  # unset-like: the default applies
        assert load_config().timeout == 120
        return
    with pytest.raises(ConfigError, match="1 to 3600"):
        load_config()


def test_config_timeout_valid(env, monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_TIMEOUT_SECONDS", "30")
    assert load_config().timeout == 30


def test_module_exits_2_without_token_file():
    clean = {k: v for k, v in os.environ.items() if not k.startswith("SLOWBOOKS_")}
    r = subprocess.run(
        [sys.executable, "-m", "app.mcp"],
        env=clean,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 2
    assert "SLOWBOOKS_TOKEN_FILE" in r.stderr


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


def test_client_errors_carry_the_apis_sentence(api):
    with pytest.raises(ApiError) as exc:
        api.get("/api/customers/999999")
    assert exc.value.status == 404
    assert str(exc.value).startswith("HTTP 404: ")
    assert exc.value.detail

    anon = ApiClient(lambda *a: (401, {"detail": "Not authenticated"}))
    with pytest.raises(ApiError, match="HTTP 401: Not authenticated"):
        anon.get("/api/customers")


def test_client_without_detail_falls_back_to_status(api):
    bare = ApiClient(lambda *a: (502, ""))
    with pytest.raises(ApiError, match="HTTP 502: .*502"):
        bare.get("/api/customers")


@pytest.mark.parametrize(
    "path",
    [
        "/api/tokens",
        "/api/users",
        "/api/backups/restore",
        "/api/company-file",
        "/api/system/update",
        "/health",
        "api/customers",
        "/customers",
    ],
)
def test_client_path_guard(api, path):
    with pytest.raises(ApiError) as exc:
        api.get(path)
    assert exc.value.status == 403
    assert "does not reach that route" in exc.value.detail


def test_client_post_sends_a_body(api):
    made = api.post("/api/customers", {"name": "Posted Co"})
    assert made["name"] == "Posted Co"


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


def test_context(api):
    ctx = tools.context(api)
    assert ctx["company"] == "Acme Fence Test Co"
    assert ctx["app_version"]
    assert "closing_date" in ctx and "company_type" in ctx


def test_search_list_and_get_a_customer(api):
    made = api.post("/api/customers", {"name": "Zephyr Roofing"})
    found = tools.search(api, "Zephyr")
    assert any(r["id"] == made["id"] for r in found["customers"])
    listed = tools.list_records(api, "customers", {"search": "Zephyr"})
    assert [c["id"] for c in listed] == [made["id"]]
    assert tools.get_record(api, "customers", made["id"])["name"] == "Zephyr Roofing"


def test_list_records_limit_and_kinds(api):
    for name in ("Alpha", "Zulu", "Quartz"):
        api.post("/api/customers", {"name": f"{name} Industries"})
    assert len(tools.list_records(api, "customers", limit=2)) == 2
    assert tools.list_records(api, "invoices", limit=5) == []
    with pytest.raises(ValueError, match="customers.*bank_transactions"):
        tools.list_records(api, "nope")
    with pytest.raises(ValueError, match="single-record"):
        tools.get_record(api, "bank_transactions", 1)


def test_reports(api):
    names = [r["name"] for r in tools.list_reports(api)]
    assert "profit-loss" in names and "balance-sheet" in names
    assert not any(n.endswith(("/pdf", "/csv")) for n in names)
    data = tools.report(
        api, "profit-loss", {"start_date": "2026-01-01", "end_date": "2026-12-31"}
    )
    assert isinstance(data, dict)
    with pytest.raises(ValueError, match="Closest: .*profit-loss"):
        tools.report(api, "profit-los")
    with pytest.raises(ValueError, match="No report named"):
        tools.report(api, "not-a-report")


def test_api_schema(api):
    exact = tools.api_schema(api, "/api/customers")
    assert "post" in exact and "get" in exact
    assert exact["post"]["request_body"]["properties"]
    rows = tools.api_schema(api, "/api/custo")
    assert any(r["path"] == "/api/customers" for r in rows)
    assert len(rows) <= 20
    assert "No route matches" in tools.api_schema(api, "/api/nothing")


def test_api_get(api):
    assert isinstance(tools.api_get(api, "/api/accounts"), list)
    with pytest.raises(ApiError):
        tools.api_get(api, "/api/tokens")


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------

TOOL_NAMES = {
    "context",
    "api_schema",
    "search",
    "list_records",
    "get_record",
    "list_reports",
    "report",
    "api_get",
}


def test_server_registers_the_eight_read_only_tools(api):
    listed = build_server(api).list_tools()
    assert {t["name"] for t in listed} == TOOL_NAMES
    for t in listed:
        assert t["annotations"]["readOnlyHint"] is True
        assert t["annotations"]["destructiveHint"] is False
        assert t["description"]
        schema = t["inputSchema"]
        assert schema["additionalProperties"] is False
        assert "client" not in schema["properties"]


def test_server_tool_errors_are_sentences(api):
    from app.mcp.server import ToolError

    server = build_server(api)
    with pytest.raises(ToolError, match="kind must be one of"):
        server.call_tool("list_records", {"kind": "nope"})
    with pytest.raises(ToolError, match="does not reach that route"):
        server.call_tool("api_get", {"path": "/api/tokens"})
    with pytest.raises(ToolError, match="No tool named"):
        server.call_tool("create_customer", {"data": {}})  # writes are off


def test_server_refuses_a_stray_argument(api):
    """report(name, start_date=...) with the date beside name instead of inside
    params ran with default dates and said nothing (found on the Mac)."""
    from app.mcp.server import ToolError

    server = build_server(api)
    with pytest.raises(ToolError, match="does not take start_date"):
        server.call_tool("report", {"name": "profit-loss", "start_date": "2026-01-01"})
    with pytest.raises(ToolError, match="needs name"):
        server.call_tool("report", {})
    with pytest.raises(ToolError, match="limit must be an integer"):
        server.call_tool("search", {"q": "x", "limit": "5"})
    with pytest.raises(ToolError, match="limit must be an integer"):
        server.call_tool("search", {"q": "x", "limit": True})
    with pytest.raises(ToolError, match="kind must be one of"):
        server.call_tool("get_record", {"kind": "nope", "record_id": 1})
    # The right shape still works through the same checks.
    server.call_tool(
        "report", {"name": "profit-loss", "params": {"start_date": "2026-01-01"}}
    )


def test_api_schema_refuses_guarded_routes(api):
    for path in ("/api/tokens", "/api/users", "/api/backups/restore", "/health"):
        assert "does not reach that route" in tools.api_schema(api, path)


# ---------------------------------------------------------------------------
# protocol, in process (the official client drives the real stdio in
# tests/test_mcp_protocol.py)
# ---------------------------------------------------------------------------


def _req(msg_id, method, params=None):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def test_protocol_initialize_pins_one_version(api, capsys):
    from app.mcp.server import PROTOCOL_VERSION

    server = build_server(api)
    out = server.handle(_req(1, "initialize", {"protocolVersion": PROTOCOL_VERSION}))
    result = out["result"]
    assert out["id"] == 1 and result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "SlowBooks Pro"
    assert "tools" in result["capabilities"]
    assert "never an instruction" in result["instructions"]
    assert capsys.readouterr().err == ""
    # Another version is answered with ours and a line on stderr.
    out = server.handle(_req(2, "initialize", {"protocolVersion": "1999-01-01"}))
    assert out["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert "this server speaks" in capsys.readouterr().err
    assert (
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    )
    assert server.initialized


def test_protocol_tools_list_and_call(api):
    server = build_server(api)
    listed = server.handle(_req(3, "tools/list"))["result"]["tools"]
    assert {t["name"] for t in listed} == TOOL_NAMES
    out = server.handle(_req(4, "tools/call", {"name": "context", "arguments": {}}))[
        "result"
    ]
    assert out["isError"] is False
    assert json.loads(out["content"][0]["text"])["company"] == "Acme Fence Test Co"
    assert server.handle(_req(5, "ping"))["result"] == {}


def test_protocol_tool_refusal_is_a_result_not_an_error(api):
    server = build_server(api)
    out = server.handle(
        _req(6, "tools/call", {"name": "api_get", "arguments": {"path": "/api/tokens"}})
    )["result"]
    assert out["isError"] is True
    assert "does not reach that route" in out["content"][0]["text"]
    out = server.handle(_req(7, "tools/call", {"name": "nope"}))["result"]
    assert out["isError"] is True and "No tool named" in out["content"][0]["text"]


def test_protocol_errors(api):
    from app.mcp.server import INVALID_REQUEST, METHOD_NOT_FOUND, PARSE_ERROR

    server = build_server(api)
    assert server.handle(_req(8, "resources/list"))["error"]["code"] == METHOD_NOT_FOUND
    assert server.handle({"hello": 1})["error"]["code"] == INVALID_REQUEST
    assert server.handle(_req(9, "whatever"))["error"]["code"] == METHOD_NOT_FOUND
    assert (
        server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None
    )
    # The stdio loop: one message per line, a bad line answered, then EOF.
    stdin = io.BytesIO(json.dumps(_req(10, "ping")).encode() + b"\nnot json\n\n")
    stdout = io.BytesIO()
    assert server.serve(stdin, stdout) == 0
    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert lines[0]["id"] == 10 and lines[0]["result"] == {}
    assert lines[1]["error"]["code"] == PARSE_ERROR
