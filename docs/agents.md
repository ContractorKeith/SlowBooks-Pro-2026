# Agents: the local MCP server

SlowBooks Pro ships a small MCP server so an AI agent (Claude Code, Codex,
Claude Desktop) can work your books. It runs on your machine over stdio,
talks to the running app on loopback with an API token, and reaches the API
only: it adds nothing to the books and has no database access of its own.

## Setup

1. Install the extra: `pip install -r requirements-mcp.txt`. The desktop
   installers already include it.
2. Under Settings, API Tokens, make a token. Pick the role the agent should
   have; the API enforces it. The token is shown once, with these steps.
3. Save the token in a private file (one line, starting with `sbp_`):

   ```
   printf '%s\n' 'sbp_...' > ~/.slowbooks-token && chmod 600 ~/.slowbooks-token
   ```

4. Start the app, then connect the agent.

**Claude Code**

```
claude mcp add slowbooks -e SLOWBOOKS_TOKEN_FILE=~/.slowbooks-token -- python -m app.mcp
```

**Codex and Claude Desktop** take the same server in their config JSON:

```json
{
  "mcpServers": {
    "slowbooks": {
      "command": "python",
      "args": ["-m", "app.mcp"],
      "env": { "SLOWBOOKS_TOKEN_FILE": "/home/you/.slowbooks-token" }
    }
  }
}
```

Use the full path to the token file here; these apps do not expand `~`.
Run it from the checkout (or point `command` at the Python that has the
extra installed).

**Desktop install.** `SlowBooksPro --mcp` starts the same server. It finds
the app on the port in your `.env` (else 3001) and reads the token from
`mcp-token` in the data directory:

| Platform | Default token file |
|----------|--------------------|
| Windows | `%LOCALAPPDATA%\SlowBooksPro\data\mcp-token` |
| macOS | `~/Library/Application Support/SlowBooksPro/data/mcp-token` |
| Linux | `~/.slowbookspro/data/mcp-token` |

`--mcp` needs the app already running. It does not open a company, migrate
or start a server.

## Settings

| Variable | Default | Meaning |
|----------|---------|---------|
| `SLOWBOOKS_TOKEN_FILE` | none (required) | File holding the token, one line |
| `SLOWBOOKS_URL` | `http://127.0.0.1:3001` | The running app; loopback `http://` only |
| `SLOWBOOKS_TIMEOUT_SECONDS` | `120` | Per-request timeout, 1 to 3600 |

The token comes from a file and nowhere else, so it never shows in a process
list or an agent's config. On macOS and Linux the file must not be readable
by others.

## Tools

Reads:

| Tool | Does |
|------|------|
| `context` | Company and app facts to read before working |
| `api_schema` | How to call a route: methods, parameters and request body |
| `search` | Find records by name, number or amount across the books |
| `list_records` | List one kind of record; filters pass through as query parameters |
| `get_record` | One record by id |
| `list_reports` | The reports that can be run, with their parameters |
| `report` | Run a report by name (see `list_reports`) |
| `api_get` | Read-only GET of any other API route (see `api_schema`) |

Record kinds: customers, vendors, items, accounts, invoices, bills,
expenses, journal_entries, payments, bill_payments, bank_transactions.

Writes. Each posts the data you give it to the API as-is, so the API's own
validation answers: a missing or unknown field comes back as a sentence,
and `api_schema` on the route lists the fields. The tools are marked as
writes, so a client such as Claude Code asks you before running one.

| Tool | Does |
|------|------|
| `create_customer`, `create_vendor`, `create_item` | Master records |
| `create_invoice`, `create_bill` | Documents with lines |
| `create_expense` | A paid expense, one amount or lines |
| `create_journal_entry` | A manual journal entry; debits must equal credits |
| `record_payment`, `record_bill_payment` | Money in and out, applied to invoices or bills. Each takes a `key` (1 to 128 characters of letters, digits, `_ . : -`): the same key with the same data books once, the same key with other data is refused, so a retry after a lost answer can never pay twice |
| `void_record` | Void an invoice, bill, expense, journal entry, payment or bill payment with a reversing entry. Nothing is ever deleted |

There is no generic write tool: a route gets a tool when a real use shows
up. When a write's answer is lost (a timeout, the app restarted), the tool
says so and what to do: retry a payment with the same key; check with
`list_records` or `search` before retrying anything else.

## What a token can never do

No token can manage users or tokens, restore a backup or change the
company file, and the server refuses those routes before they are sent.
The token's role limits the rest: the agent can do what that role can.
Every change is audited as `token:<label>`, using the label you gave the
token. Revoke it under Settings, API Tokens, and the agent is locked out.

## Troubleshooting

- "SLOWBOOKS_TOKEN_FILE must name a file that holds the API token": the
  variable is unset. Set it to the token file's path.
- "The token file ... does not exist": check the path; use a full path in
  JSON configs.
- "The token file ... can be read by other users": run `chmod 600` on it.
- "The token file ... must hold one line": the file holds the token and
  nothing else, and it starts with `sbp_`.
- "SLOWBOOKS_URL must be a loopback http:// address": use
  `http://127.0.0.1:<port>`. The server never talks to another machine.
- "The MCP server needs the `mcp` extra": run
  `pip install -r requirements-mcp.txt`.
- Tools answer with a refusal: the app is not running, the token is revoked,
  or its role does not allow that. The API's own sentence says which.
