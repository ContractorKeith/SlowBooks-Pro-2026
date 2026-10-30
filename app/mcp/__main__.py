"""``python -m app.mcp``: start the stdio MCP server (standard library only)."""

import sys

from app.mcp.server import run

if __name__ == "__main__":
    sys.exit(run())
