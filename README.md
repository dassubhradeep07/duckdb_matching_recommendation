# Customer matching, review analytics, agents, and data quality

This project compares two synthetic customer datasets with DuckDB, selects
one-to-one match suggestions, supports human review and recommendation
analytics, exposes read-only data tools to AI clients, and checks data quality
with configurable Soda contracts.

The supplied raw datasets contain 10,000 records each. Scores, recommendations,
and auto-merge candidate lists are advisory. The project does not merge or
update source customer records; a high score is not a verified identity decision.

## Project areas

| Area | Location | Purpose |
| --- | --- | --- |
| Raw data | `customer_matching/data/` | Source A and source B customer CSVs |
| Matching | `customer_matching/main.py` | Candidate generation, scoring, and one-to-one selection |
| Analysis | `customer_matching/analysis.py`, `analytics/analytics_pipeline.py` | Review import, recommendations, analytics, and history |
| Human review | `human-review/`, `customer_matching/steward_review.py` | Compact steward queue and standard comments |
| Customer Data Analyst | `agent-example/` | Copilot agent, Codex instructions, skills, and MCP tools |
| Data quality | `data_quality/` | Independent Soda checks and HTML/CSV/JSON reports |
| SQL reference | [duckdb_sql_queries.txt](duckdb_sql_queries.txt) | SQL examples and reference queries |

Detailed documentation: [matching pipeline](customer_matching/README.md),
[matching rules](customer_matching/matching.md),
[agent setup](agent-example/README.md), and
[Soda data quality](data_quality/README.md).

## Environment setup on Windows

Run commands from the project root unless a section says otherwise:

```powershell
Set-Location C:\Users\SUBHRADEEP\duckdb-test
```

There are two Python environments. `.venv` contains matching, analytics,
review, and MCP dependencies. `.venv-dq` contains Soda and its DuckDB connector
so Soda installation does not change the matching environment.

For a new installation, use Python 3.13, the version used to verify this project:

```powershell
# Skip environment creation if .venv already exists.
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt

# Optional DQ environment; skip creation if .venv-dq already exists.
.\.venv\Scripts\python.exe -m venv .venv-dq
.\.venv-dq\Scripts\python.exe -m pip install -r .\data_quality\requirements.txt
```

The main dependencies are DuckDB, psutil, scikit-learn, PyYAML, and MCP.
The DQ requirements pin `soda-duckdb==4.28.0`, which also installs Soda Core.
The pinned Soda packages require Python 3.10 or newer. Commands below name
the Python executable explicitly, so environment activation is optional.

## Run matching and analysis

Run matching first and analysis second:

```powershell
.\.venv\Scripts\python.exe -m customer_matching.main
.\.venv\Scripts\python.exe -m customer_matching.analysis
```

Matching writes selected/unmatched reports, intermediate candidate scores,
and `match_run.json`. Analysis consumes those artifacts, refreshes review and
recommendation reports, appends analytics history, and generates the compact
steward YAML queue. If either raw input changes, rerun matching before
analysis. For review-only changes, rerun analysis.

Optional raw and master DQ checks can surround the existing commands:

```powershell
.\.venv-dq\Scripts\python.exe -m data_quality.run --layer raw
.\.venv\Scripts\python.exe -m customer_matching.main
.\.venv\Scripts\python.exe -m customer_matching.analysis
.\.venv-dq\Scripts\python.exe -m data_quality.run --layer master
```

DQ is invoked explicitly; it is not automatically called by matching or
analysis. These commands do not automatically stop the sequence when an
earlier command fails. For a gated script, inspect `$LASTEXITCODE` before
starting the next command and use DQ's `--fail-on-quality` when appropriate.

## Raw and master layers

| Layer | Files | Meaning |
| --- | --- | --- |
| Raw | `customer_matching/data/source_a.csv`, `customer_matching/data/source_b.csv` | Original customer inputs |
| Master | `customer_matching/matched_customers.csv`, `customer_matching/unmatched_customers.csv`, `customer_matching/auto_merge_candidates.csv` | Existing matching output reports used by master-layer DQ |

There is no persisted golden customer table. "Master" in the DQ configuration
means those three reports; it does not mean records have been merged or approved.
Both raw inputs have `customer_id`, `customer_name`, `email`, `phone`, `address`,
`city`, `state`, `postal_code`, and `country` columns.

## Matching rules and options

Candidates are generated only when normalized, nonmissing postal codes match.
Available fields are compared with Jaro-Winkler similarity and a weighted
score from 0 to 100:

| Field | Weight |
| --- | ---: |
| Customer name | 30% |
| Email | 25% |
| Phone | 20% |
| Address | 8% |
| City | 5% |
| State | 3% |
| Postal code | 6% |
| Country | 3% |

Missing fields are excluded from the weighted average and the remaining
weights are renormalized. Customer ID is retained but not scored. Eligible
pairs are considered from highest score downward, using each source/reference
row at most once. This deterministic greedy selection is not guaranteed to
find the globally optimal assignment.

The default match cutoff is 75. To change it or enable detailed logging:

```powershell
.\.venv\Scripts\python.exe -m customer_matching.main --threshold 85 --log-level DEBUG
.\.venv\Scripts\python.exe -m customer_matching.analysis
```

Analysis controls the auto-merge candidate cutoff, which defaults to 95.
The option belongs to the analysis command:

```powershell
.\.venv\Scripts\python.exe -m customer_matching.analysis --auto-merge-threshold 98
```

Both cutoffs accept values from 0 to 100. The auto-merge report includes
selected pairs meeting its cutoff, excluding reviewer-rejected pairs; it does
not perform a merge. An unmatched record's best candidate score may be high
when the competing customer was selected for another pair.

See [matching.md](customer_matching/matching.md) for normalization details,
score bands, and synthetic-data scenarios.

## Human review and recommendation analytics

### Compact steward queue

Analysis generates `human-review/steward_review.yaml` with up to 100 pending
candidates plus previously reviewed rows. Edit only:

- `steward_decision`: `approve`, `reject`, or blank.
- `steward_comment`: the reason for the decision.

Optionally fill standard comments for decided rows with blank comments:

```powershell
.\.venv\Scripts\python.exe .\human-review\fill_standard_notes.py
```

The helper does not make decisions. Rerun analysis to import steward labels
and refresh recommendations:

```powershell
.\.venv\Scripts\python.exe -m customer_matching.analysis
```

To regenerate the queue separately from the default candidate-review CSV:

```powershell
.\.venv\Scripts\python.exe .\human-review\generate.py --generate --limit 100
```

### Full candidate review CSV

`analytics/candidate_review.csv` includes every generated candidate. As an
alternative review interface, edit only:

- `review_decision`: `approved`, `rejected`, or blank.
- `review_comment`: the reason for the decision.

Keep candidate keys and other generated columns intact. Rerun analysis after
review edits. Decisions are retained by a stable candidate key when the
underlying source records have not changed. A changed record prevents an old
decision from being applied to the changed pair. Invalid decisions or
missing/duplicate review keys produce an error.

Analysis accepts custom review locations:

```powershell
.\.venv\Scripts\python.exe -m customer_matching.analysis --review-file C:\path\to\candidate_review.csv --steward-review-file C:\path\to\steward_review.yaml
```

### Recommendation model and history

A supervised logistic-regression recommendation model trains after at least
20 reviewed candidates exist, including five approvals and five rejections.
Until then, recommendations use score clusters when enough candidate data is
available, with manual review as the fallback.

`model_approval_score` is not a calibrated probability or proven accuracy
measure. Recommendations and suggested thresholds do not auto-approve
customers or change the matching cutoff. History retains run summaries, field
rules, score patterns, performance, and recommendation records.

## Soda data quality

Soda runs as a Python library through `ContractVerificationSession.execute()`
in `data_quality/run.py`. No separate Soda server, port, or Soda Cloud account
is needed for these local checks. The runner loads existing CSVs into a
temporary DuckDB snapshot, verifies native Soda Core v4 YAML contracts, writes
aggregate reports, and removes the temporary database. It does not call the
analyst MCP server or run matching/analysis.

### Run scans and view reports

```powershell
# Both layers; requires existing matching/analysis reports.
.\.venv-dq\Scripts\python.exe -m data_quality.run

# Raw-only scans do not require master reports.
.\.venv-dq\Scripts\python.exe -m data_quality.run --layer raw

# Master checks also load raw inputs for relationship checks.
.\.venv-dq\Scripts\python.exe -m data_quality.run --layer master

# Custom configuration/output and optional quality gate.
.\.venv-dq\Scripts\python.exe -m data_quality.run --config .\data_quality\datasets.yaml --output-dir .\data_quality\reports --fail-on-quality
```

| Report | Contents |
| --- | --- |
| [dq_report.html](data_quality/reports/dq_report.html) | Combined raw/master findings |
| [dq_raw.html](data_quality/reports/dq_raw.html) | Raw-layer findings |
| [dq_master.html](data_quality/reports/dq_master.html) | Master-layer findings |
| `data_quality/reports/dq_checks.csv` | One row per check with outcome, measured value, and threshold |
| `data_quality/reports/dq_report.json` | Structured summary, errors, and input/config/contract hashes |

These report links become available after scanning. Reports contain aggregate
metrics, not customer row samples. Output files are replaced on each run; use
separate output directories to retain history. A scan of only one layer leaves
older HTML for the other layer in place, so check timestamps.

| Exit code | Meaning |
| --- | --- |
| `0` | Scan completed; quality findings may still exist |
| `1` | Checks failed and `--fail-on-quality` was enabled |
| `2` | Configuration, input, SQL, or execution error |

Without `--fail-on-quality`, a failed rule is reported without gating the
pipeline. Warnings do not produce exit code 1. Missing required files are
execution errors. Empty raw inputs fail quality checks; empty master reports
are allowed when their cross-report reconciliation checks pass. Input hashes
detect changes during scanning; use stable files when other processes write
pipeline reports concurrently. Soda Cloud publishing is disabled, and anonymous
Soda telemetry is disabled by default.

PowerShell helpers are available:

```powershell
.\data_quality\setup.ps1
.\data_quality\run-dq.ps1 -Layer all -FailOnQuality
```

If execution policy blocks the helpers, use the direct Python commands above;
changing machine policy is not required.

### Configure rules without changing Python

Edit [data_quality/datasets.yaml](data_quality/datasets.yaml) for CSV paths,
layer assignments, and contract locations. CSV paths resolve against
`project_root`; `project_root` and contract paths resolve against the
configuration file's directory. Keep dataset names consistent with relationship
queries.

Edit the native Soda YAML contracts in [data_quality/contracts/](data_quality/contracts/)
to add/remove checks or change thresholds and severity:

```yaml
- metric:
    name: Missing or blank customer_name
    qualifier: missing_customer_name
    expression: COUNT(*) FILTER (WHERE customer_name IS NULL OR TRIM(customer_name) = '')
    threshold:
      must_be: 0
      level: fail
```

Use `level: warn` for advisory checks. Removing a check disables it; a contract
with no checks is an execution error. Give multiple metric checks distinct
`qualifier` values.

| Layer | Default checks |
| --- | --- |
| Raw | Schema, nonempty input, unique/nonblank IDs, required names/postal codes, optional-field completeness, basic email/postal syntax, contact availability |
| Master | Schema, one-to-one ID uniqueness, unmatched source/ID uniqueness, score ranges, source/review values, raw-ID references, exact matched/unmatched coverage, auto-merge subset and score consistency |

Default DQ score expectations are 75-100 for matches and 95-100 for auto-merge
candidates. They are independent assertions, not matching settings. If a run
uses different cutoffs, adjust the corresponding YAML assertions. Format
warnings reflect the supplied synthetic data rather than universal address or
email-deliverability validation. Values are loaded as VARCHAR to preserve IDs
and leading zeros; numeric checks use `TRY_CAST`.

The current runner accepts CSV inputs. pandas or PySpark integration would
require a data-loading/connector adaptation and potentially schema or SQL
changes; those integrations are not implemented in this project.

## Customer Data Analyst: Copilot, Codex, and MCP

The analyst uses its client's model and agent loop, project instructions, and
the local `customer-data` MCP tools. LangChain and LangGraph are not project
dependencies.

```text
Question -> AI client follows instructions -> list_datasets
         -> query_sql -> DuckDB result -> answer based on returned rows
```

| File | Responsibility |
| --- | --- |
| `agent-example/.github/agents/customer-data.agent.md` | Named Customer Data Analyst agent for Copilot |
| `agent-example/AGENTS.md` | Workspace instructions for Codex and compatible clients |
| `agent-example/.agents/skills/duckdb-customer-qa/SKILL.md` | Analyst procedure for Codex |
| `agent-example/.github/skills/duckdb-customer-qa/SKILL.md` | Copilot-facing skill copy |
| `agent-example/.vscode/mcp.json` | VS Code/Copilot MCP startup configuration |
| `agent-example/setup-codex.ps1` | Registers the MCP server with Codex CLI |
| `agent-example/.github/skills/duckdb-customer-qa/scripts/mcp_server.py` | Exposes `list_datasets` and `query_sql` over stdio |
| `agent-example/.github/skills/duckdb-customer-qa/scripts/query_data.py` | Loads CSVs, validates queries, and returns DuckDB results |

### VS Code Copilot

Open `C:\Users\SUBHRADEEP\duckdb-test\agent-example` as the VS Code workspace,
then select **Customer Data Analyst** in Copilot Chat. This workspace location
matters because `.vscode/mcp.json` uses `${workspaceFolder}` to locate Python
and the server.

Example question: "Use list_datasets, then count customers in source_a and
source_b with query_sql. State the tables and filters used."

### Codex

Codex uses its own MCP configuration. Its default user location on this
machine is `C:\Users\SUBHRADEEP\.codex\config.toml`; it does not read the
Copilot server definition from `.vscode/mcp.json`. Both configurations point
to the same server code independently.

If Codex CLI is installed and `codex` is available in PowerShell, register it:

```powershell
Set-Location C:\Users\SUBHRADEEP\duckdb-test\agent-example
.\setup-codex.ps1
codex mcp list
```

Restart Codex after registration and start a conversation in `agent-example`
so it can read `AGENTS.md` and the skill. The Copilot agent file is not a
named-agent selector for Codex; instructions and MCP tools provide the analyst
behavior there.

If PowerShell reports that `codex` is not recognized, fix CLI installation or
PATH before running the registration script. An installed IDE extension does
not guarantee that an ordinary PowerShell terminal can find a `codex` command.
For manual MCP configuration, use the Python command and server argument below.

Return to the project root before running the other commands in this README:

```powershell
Set-Location C:\Users\SUBHRADEEP\duckdb-test
```

### Other Windows applications and headless use

Any compatible MCP client can start the same server using:

| Setting | Value |
| --- | --- |
| Transport | stdio |
| Command | `C:\Users\SUBHRADEEP\duckdb-test\.venv\Scripts\python.exe` |
| Argument | `C:\Users\SUBHRADEEP\duckdb-test\agent-example\.github\skills\duckdb-customer-qa\scripts\mcp_server.py` |
| Working directory | `C:\Users\SUBHRADEEP\duckdb-test\agent-example` |

The MCP server runs without a user interface and is normally started by its
client. It expects MCP protocol messages, not plain-English terminal input.
The AI client supplies natural-language reasoning and model authentication.
There is no dedicated headless natural-language launcher in this repository;
unattended agent execution must be configured in the chosen client. Soda DQ
already runs independently from the command line without an AI client.

### Tools and refresh behavior

`list_datasets` discovers tables and columns. `query_sql` accepts one read-only
DuckDB `SELECT` and returns at most 500 rows. External file access is disabled
after data loading. Answers should state the tables and filters used and prefer
aggregate results.

The server requires both raw inputs and loads whichever generated tables exist
at startup: `matched_customers`, `unmatched_customers`, `candidate_scores`,
`candidate_score_summary`, `auto_merge_candidates`, `candidate_review`, and
`recommendation`. Restart the server/client after regenerating reports to
refresh its in-memory snapshot. DQ reports and analytics history are not
registered as MCP tables by the current server.

## Output files and logging

| File or directory | Produced by | Contents |
| --- | --- | --- |
| `customer_matching/matched_customers.csv` | Matching | Selected pairs and scores |
| `customer_matching/unmatched_customers.csv` | Matching | Unselected records and best candidate scores |
| `customer_matching/candidate_scores.csv` | Matching | Intermediate candidate scores and similarities |
| `customer_matching/candidate_score_summary.csv` | Matching | Candidate totals and score bands |
| `customer_matching/match_run.json` | Matching | Run metadata required by analysis |
| `customer_matching/performance_metrics.csv` | Matching | Operation timings and memory measurements |
| `customer_matching/customer_matching.log` | Matching/analysis logging | Settings, progress, and errors |
| `customer_matching/auto_merge_candidates.csv` | Analysis | High-score selected candidates excluding rejected pairs |
| `analytics/candidate_review.csv` | Analysis | Full review queue, scores, clusters, and recommendations |
| `analytics/recommendation.csv` | Analysis | Current recommendation and rationale |
| `analytics/history/` | Matching/analysis | Run, rule, pattern, performance, and recommendation history |
| `human-review/steward_review.yaml` | Analysis or review generator | Compact editable steward queue |
| `data_quality/reports/` | Explicit DQ scan | HTML, CSV, and JSON quality reports |

Generated reports are refreshed by their owning commands. Preserve reviewer
edits in the designated decision/comment fields rather than editing generated
scores or identifiers. Analytics history is retained separately from current
reports.

## Regenerate synthetic raw data

The generator replaces both raw CSVs with deterministic synthetic inputs:

```powershell
.\.venv\Scripts\python.exe .\customer_matching\generate_data.py
```

After regeneration, rerun matching, analysis, and any desired DQ scans. Agent
sessions keep their previous snapshot until the MCP server restarts.

## Verification and troubleshooting

Run DQ integration tests in the DQ environment:

```powershell
.\.venv-dq\Scripts\python.exe -m unittest data_quality.test_run
```

These tests execute Soda against temporary fixtures: valid inputs, invalid
raw/master data, missing files, invalid contracts, and legitimate empty master
outputs. They do not regenerate the project's matching/analytics reports.

| Symptom | Action |
| --- | --- |
| Analysis reports missing intermediate files | Run matching first; rerun it after source changes |
| `No module named soda_core` | Use `.venv-dq` Python and install `data_quality/requirements.txt` |
| DQ score failures after changing matching settings | Review the independent expectations in the YAML contracts |
| PowerShell blocks a DQ `.ps1` helper | Use the documented direct Python entry point |
| `codex` is not recognized | Install/expose the CLI on PATH before registration |
| Copilot/Codex cannot see customer tools | Check that client's MCP configuration and start a new session |
| Analyst answers use old data | Restart its MCP server after regenerating outputs |
| Review decisions are rejected | Use the documented decision values and preserve candidate keys |

The data is synthetic. Human review is judgment on that data; recommendations,
DQ passes, and similarity scores do not independently prove customer identity.
