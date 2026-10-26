# Customer data workspace

For questions about customer records or matching results, use the local
`customer-data` MCP server instead of manually opening CSV files.

- Call `list_datasets` to discover available tables and columns.
- Use `query_sql` with a single read-only DuckDB `SELECT`.
- Base factual answers on returned query rows; report the table and filters.
- Prefer aggregate queries and avoid exposing personal data unnecessarily.
- Never write to datasets or describe model recommendations as verified
  identity decisions.

Follow the `duckdb-customer-qa` skill in
`.agents/skills/duckdb-customer-qa/SKILL.md`.
