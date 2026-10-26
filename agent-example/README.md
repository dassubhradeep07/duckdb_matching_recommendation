# Customer Data Analyst

This folder contains one read-only DuckDB MCP server that can be used by
VS Code Copilot, Codex CLI, and other Windows applications that support MCP
over stdio. It queries datasets in the parent `duckdb-test` project.

## VS Code Copilot

1. Install the project dependencies if needed:

   ```powershell
   Set-Location C:\Users\SUBHRADEEP\duckdb-test
   .\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
   ```

2. Open `C:\Users\SUBHRADEEP\duckdb-test\agent-example` as a VS Code
   workspace.
3. Open Copilot Chat, select **Customer Data Analyst**, and ask a question.
   The workspace MCP server is configured in `.vscode/mcp.json`.

## Codex CLI

From PowerShell:

```powershell
Set-Location C:\Users\SUBHRADEEP\duckdb-test\agent-example
.\setup-codex.ps1
```

Restart Codex after registration. Verify with `codex mcp list`, then ask it
to call `list_datasets` or `query_sql`.

## Other MCP-compatible Windows applications

Configure an MCP server using stdio with:

- **Command:** `C:\Users\SUBHRADEEP\duckdb-test\.venv\Scripts\python.exe`
- **Argument:** `C:\Users\SUBHRADEEP\duckdb-test\agent-example\.github\skills\duckdb-customer-qa\scripts\mcp_server.py`
- **Working directory:** `C:\Users\SUBHRADEEP\duckdb-test\agent-example`

The server offers `list_datasets` and `query_sql`. `query_sql` accepts one
read-only `SELECT`, with a maximum result limit of 500 rows. It loads the
source CSVs and whichever match/analytics reports currently exist when the
server starts. Restart the client/server after regenerating reports to
refresh its data snapshot.

The Copilot agent definition is in `.github/agents/customer-data.agent.md`.
Shared Codex/agent instructions are in `AGENTS.md` and
`.agents/skills/duckdb-customer-qa/SKILL.md`; Copilot's skill copy is in
`.github/skills/duckdb-customer-qa/SKILL.md`.
