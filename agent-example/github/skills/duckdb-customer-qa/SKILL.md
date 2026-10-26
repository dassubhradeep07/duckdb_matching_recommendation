---
name: duckdb-customer-qa
description: "Query the local customer matching datasets with read-only DuckDB and answer customer analytics questions. Use for source customer counts, matching results, candidate scores, review decisions, and score summaries."
argument-hint: "Ask a question about the customer matching data"
user-invocable: true
---

# Customer Matching Data Q&A

Use this skill when a user asks a natural-language question that can be
answered from the local customer-matching CSV datasets.

## MCP tools

Use the configured `customer-data` MCP server:

- Call `list_datasets` first to see available tables and columns.
- Convert the question into one DuckDB `SELECT` and call `query_sql` with the
  SQL and a suitable row limit.

The server returns column names, rows, and truncation information. The
default output is capped at 100 rows; use a limit from 1 to 500. Prefer
aggregates for summaries and `ORDER BY` for ranked results.

For local troubleshooting outside an MCP client, the same query tool can be
run directly:

```powershell
..\.venv\Scripts\python.exe .\.github\skills\duckdb-customer-qa\scripts\query_data.py --schema
..\.venv\Scripts\python.exe .\.github\skills\duckdb-customer-qa\scripts\query_data.py --sql "SELECT count(*) AS customer_count FROM source_a"
```

## Available datasets

The runner registers the fixed local customer inputs as `source_a` and
`source_b`. When the matching pipeline has generated them, it also registers
`matched_customers`, `unmatched_customers`, `candidate_scores`,
`candidate_score_summary`, `auto_merge_candidates`, `candidate_review`, and
`recommendation`. `--schema` reports the tables and columns available in this
run. All CSV files are loaded from the parent `duckdb-test` project; do not
construct file paths or query arbitrary files.

## Rules

- Execute only a single read-only `SELECT` query using the available tables.
- Never use INSERT, UPDATE, DELETE, CREATE, DROP, COPY, ATTACH, or multiple
  statements.
- Never ask the runner to read a path or external source.
- Use explicit filters and explain important date/score thresholds.
- Report the returned result faithfully; do not infer missing data.
- Distinguish model recommendations from human-approved decisions.
- Do not expose raw personal customer details unless they are needed to answer
  the user's question. Prefer aggregate results.
- If a table is unavailable, explain which pipeline command creates it.

## Examples

Count source records:

```sql
SELECT 'source_a' AS dataset, count(*) AS row_count FROM source_a
UNION ALL
SELECT 'source_b', count(*) FROM source_b
```

Summarize candidate scores:

```sql
SELECT score_band, candidate_pair_count, eligible_pair_count, average_score
FROM candidate_score_summary
ORDER BY candidate_pair_count DESC
```

Count steward labels in the full candidate review:

```sql
SELECT review_decision, count(*) AS candidate_count
FROM candidate_review
GROUP BY review_decision
ORDER BY candidate_count DESC
```

List top matches:

```sql
SELECT match_score, source_customer_id, source_customer_name,
       reference_customer_id, reference_customer_name
FROM matched_customers
ORDER BY match_score DESC
LIMIT 20
```
