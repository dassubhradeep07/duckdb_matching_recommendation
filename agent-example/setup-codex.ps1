$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $workspace
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$server = Join-Path $workspace ".github\skills\duckdb-customer-qa\scripts\mcp_server.py"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python environment not found: $python"
}
if (-not (Test-Path -LiteralPath $server -PathType Leaf)) {
    throw "MCP server script not found: $server"
}
if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    throw "Codex CLI is not installed or not on PATH. Install Codex CLI, reopen PowerShell, and run this script again."
}

& codex mcp add customer-data -- $python $server
if ($LASTEXITCODE -ne 0) {
    throw "Codex could not register the customer-data MCP server (exit code $LASTEXITCODE)."
}

Write-Output "Registered customer-data for Codex."
Write-Output "Restart Codex, then ask it to call list_datasets or query_sql."
