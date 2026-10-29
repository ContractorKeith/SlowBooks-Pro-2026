# ============================================================================
# The MCP server: registers the tools over an ApiClient and runs stdio. The
# only module that imports the SDK. A refusal from the API reaches the agent
# as the API's own sentence, via the SDK's tool error.
# ============================================================================

import inspect
import sys
from functools import wraps

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from app.mcp import tools
from app.mcp.client import ApiClient, ApiError, urllib_transport
from app.mcp.config import ConfigError, load_config

INSTRUCTIONS = (
    "Tools for working the books of a running SlowBooks Pro. Call `context` "
    "first. Amounts are decimal dollars as the API returns them; dates are "
    "ISO (YYYY-MM-DD). The API enforces the token's role, the closing date "
    "and validation: a refusal is a sentence to relay to the user, not a "
    "reason to retry. Writes post through the token's role and are audited as "
    "token:<label>. Use a stable `key` for every payment so a retry can never "
    "book it twice; void instead of delete."
)

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, openWorldHint=False
)


_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)


def build_server(client: ApiClient) -> MCPServer:
    server = MCPServer("SlowBooks Pro", instructions=INSTRUCTIONS)

    def register(fn, annotations=_READ_ONLY):
        """Register tools.<fn> with the client bound; errors become sentences."""

        @wraps(fn)
        def tool(*args, **kwargs):
            try:
                return fn(client, *args, **kwargs)
            except (ApiError, ValueError) as exc:
                raise ToolError(str(exc)) from None

        # The client is bound, so hide it from the schema the agent sees.
        sig = inspect.signature(fn)
        tool.__signature__ = sig.replace(parameters=list(sig.parameters.values())[1:])
        server.tool(annotations=annotations)(tool)

    for fn in (
        tools.context,
        tools.api_schema,
        tools.search,
        tools.list_records,
        tools.get_record,
        tools.list_reports,
        tools.report,
        tools.api_get,
    ):
        register(fn)
    for fn in (
        tools.create_customer,
        tools.create_vendor,
        tools.create_item,
        tools.create_invoice,
        tools.create_bill,
        tools.create_expense,
        tools.create_journal_entry,
    ):
        register(fn, _WRITE)
    register(tools.record_payment, _IDEMPOTENT)
    register(tools.record_bill_payment, _IDEMPOTENT)
    register(tools.void_record, _DESTRUCTIVE)
    return server


def run() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    build_server(ApiClient(urllib_transport(config))).run(transport="stdio")
    return 0
