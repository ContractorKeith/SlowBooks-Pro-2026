"""The local MCP server (#655): config, the loopback client, the read tools.

The tools run against the app in-process: a transport over a cookie-less
TestClient with a real bookkeeper ApiToken, so the Bearer path through the
middleware is what every tool exercises."""

import asyncio
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
    assert "No route matches" in tools.api_schema(api, "/nothing")


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
    server = build_server(api)
    listed = asyncio.run(server.list_tools())
    assert TOOL_NAMES <= {t.name for t in listed}
    for t in listed:
        if t.name not in TOOL_NAMES:
            continue
        assert t.annotations.read_only_hint is True
        assert t.annotations.destructive_hint is False
        assert "client" not in t.input_schema.get("properties", {})


def test_server_tool_errors_are_sentences(api):
    from mcp.server.mcpserver.exceptions import ToolError

    server = build_server(api)
    with pytest.raises(ToolError, match="Unknown kind"):
        asyncio.run(server.call_tool("list_records", {"kind": "nope"}))
    with pytest.raises(ToolError, match="does not reach that route"):
        asyncio.run(server.call_tool("api_get", {"path": "/api/tokens"}))
