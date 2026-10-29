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
    "reason to retry. This server has read tools only."
)

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, openWorldHint=False
)


def build_server(client: ApiClient) -> MCPServer:
    server = MCPServer("SlowBooks Pro", instructions=INSTRUCTIONS)

    def register(fn):
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
        server.tool(annotations=_READ_ONLY)(tool)

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
    return server


def run() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    build_server(ApiClient(urllib_transport(config))).run(transport="stdio")
    return 0
