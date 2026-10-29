"""``python -m app.mcp``: start the stdio MCP server."""

import sys


def main() -> int:
    try:
        from app.mcp.server import run
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".")[0] == "mcp":
            sys.stderr.write(
                "The MCP server needs the `mcp` extra: "
                "pip install -r requirements-mcp.txt\n"
            )
            return 2
        raise
    return run()


if __name__ == "__main__":
    sys.exit(main())
