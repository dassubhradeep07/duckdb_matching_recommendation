---
name: duckdb-customer-qa
description: "Answer customer matching data questions through the local DuckDB MCP tools. Use for source counts, match outcomes, score bands, review decisions, and analytics."
---

# DuckDB customer data questions

Use the `customer-data` MCP server for data answers:

1. Call `list_datasets` to discover tables/columns available in this run.
2. Write one DuckDB `SELECT` using those tables.
3. Call `query_sql` with that SQL. Limit results to what the user needs.
4. Summarize only the returned rows. State relevant filters and thresholds.

The server loads `source_a` and `source_b` plus any generated matching and
analytics reports present at server startup. If a requested report table is
missing, tell the user which matching/analysis step creates it and ask them to
restart the MCP server after the report is generated.

The query tool accepts one read-only `SELECT`, returns at most 500 rows, and
blocks external file reads. Do not use or request any write operation.

Prefer aggregates over raw customer details. Distinguish candidate match
scores and model recommendations from steward-verified identity decisions.
Never claim a recommendation threshold changed the production matching rule.
