"""Local MCP server: an agent works the books through the API (#655).

Run with ``python -m app.mcp``. Stdio transport, loopback HTTP to the
running app, an API token from a private file. Nothing here touches the
database or the models: every tool is a request the browser could make,
under the token's role, audited as ``token:<label>``.

The ``mcp`` SDK is an extra (requirements-mcp.txt); importing this package
does not import it, so the rest of the app never depends on it.
"""
