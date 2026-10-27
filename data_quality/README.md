# Soda data quality for raw and master layers

This is an independent, opt-in quality workflow. It reads existing CSVs into
a temporary DuckDB snapshot, verifies native Soda Core v4 YAML contracts,
and writes aggregate reports. It does not import or run matching/analytics,
modify customer records, regenerate reports, apply review decisions, or change
matching thresholds. The original `.venv` and requirements are unchanged.

## Layers

| Layer | Datasets |
| --- | --- |
| Raw | `customer_matching/data/source_a.csv`, `source_b.csv` |
| Master | `customer_matching/matched_customers.csv`, `unmatched_customers.csv`, `auto_merge_candidates.csv` |

The project has no persisted golden customer table. Here, "master" means the
three existing matching output reports, as selected for this integration.
Auto-merge candidates remain recommendations, not verified identities or merges.

## Install and run on Windows

From `C:\Users\SUBHRADEEP\duckdb-test`:

```powershell
# One-time install into a separate .venv-dq environment.
.\data_quality\setup.ps1

# Read existing raw and master files and produce reports.
.\data_quality\run-dq.ps1

# Scan raw inputs before running matching, even if master reports do not exist.
.\data_quality\run-dq.ps1 -Layer raw

# Scan master reports and their relationships to raw inputs.
.\data_quality\run-dq.ps1 -Layer master

# Optional: return exit code 1 when a quality rule fails.
.\data_quality\run-dq.ps1 -FailOnQuality
```

Soda is already installed in `.venv-dq` on this machine. Setup is needed only
when recreating the environment. Soda is pinned to `4.28.0`; contracts use v4
syntax, not SodaCL v3. Python 3.10+ is required by the pinned packages; the
installation and scans were verified with this project's Python 3.13.

The Python entry point works without activating either environment:

```powershell
.\.venv-dq\Scripts\python.exe -m data_quality.run --layer all
.\.venv-dq\Scripts\python.exe -m data_quality.run --config .\data_quality\datasets.yaml --output-dir .\data_quality\reports
```

If PowerShell execution policy blocks `.ps1` helpers, use the Python entry
point above; no policy change is needed. To recreate the DQ environment
without running a PowerShell script:

```powershell
.\.venv\Scripts\python.exe -m venv .venv-dq
.\.venv-dq\Scripts\python.exe -m pip install -r .\data_quality\requirements.txt
```

Exit codes: `0` means the scan completed (warnings/quality failures may exist),
`1` means quality checks failed with `--fail-on-quality`, and `2` means a
configuration, input, SQL, or execution error. A failed check is not an engine
error. Inspect the report's `status` and `summary` in automation.

## Reports

Default output directory: `data_quality/reports/`.

- `dq_report.html`: combined report with dataset row counts and every rule.
- `dq_raw.html`, `dq_master.html`: layer views from that run.
- `dq_checks.csv`: one row per check, including outcome, measured value, and threshold.
- `dq_report.json`: machine-readable summary, checks, errors, input hashes, and
  config/contract hashes for traceability.

Report files in the selected output directory are replaced on each scan. Use
`--output-dir`/`-OutputDir` to retain separate historical runs. A layer-specific
run only writes that layer's HTML and the combined files; any other layer HTML
from an older run keeps its original timestamp. Missing required files are
reported as errors. Empty master reports are allowed; empty raw inputs fail.

Reports contain aggregate metrics and column names, not failed customer rows.
Soda Cloud publishing is disabled; no account/key is needed. Anonymous Soda
telemetry is disabled by default. Input hashes are compared before and after
scanning; concurrent file changes produce an execution error. Stop writers or
scan stable copies when running DQ alongside an active pipeline.

## Configure rules without changing Python

Edit `datasets.yaml` to change CSV locations, layer assignments, or contract
paths. CSV paths are relative to `project_root`; contract paths and
`project_root` are relative to the configuration file. An absolute path works
too. Keep the five dataset names stable because relationship checks refer to
them in SQL.

Edit the five native Soda YAML files in `contracts/` to add/remove checks or
change thresholds and severity. Every contract targets
`customer_dq/main/<dataset-name>`. Removing a check disables it; removing every
check is treated as an error. Multiple metric checks use distinct `qualifier`
values so Soda can distinguish them.

Example rule:

```yaml
- metric:
    name: Missing or blank customer_name
    qualifier: missing_customer_name
    expression: COUNT(*) FILTER (WHERE customer_name IS NULL OR TRIM(customer_name) = '')
    threshold:
      must_be: 0
      level: fail  # change to warn for an advisory check
```

The default raw contracts check schema, nonempty inputs, unique/nonblank IDs,
required names/postal codes, optional-field completeness, basic email and US
five-digit postal syntax, and available contact methods. Format rules are
warnings and tailored to the supplied synthetic data, not universal address
or deliverability validation.

Master contracts check schema, unique selected IDs, composite uniqueness for
unmatched records, numeric score ranges, valid source/review values, references
back to raw IDs, exact per-source matched/unmatched coverage, and auto-merge
pair/score consistency with the selected matches. Empty master reports are
valid if the cross-report reconciliation rules are also satisfied.

The default DQ expectations are matched scores **75–100** and auto-merge scores
**95–100**. These are editable DQ assertions in the YAML; they do not control
matching. If a pipeline run uses different cutoffs, adjust the DQ assertions
accordingly. CSV values are loaded as VARCHAR to preserve IDs, leading zeros,
and source text; numeric checks use `TRY_CAST`, including failures for
nonnumeric, missing, or out-of-range scores.

Master relationship checks require both raw inputs and all three master
reports. Raw-only scans do not require master files. Quality checks assess
structural consistency and configured business expectations, not whether two
records truly represent the same person.

## Optional place in your existing workflow

Run DQ explicitly around the existing commands; no pipeline code is changed:

```powershell
.\data_quality\run-dq.ps1 -Layer raw
.\.venv\Scripts\python.exe -m customer_matching.main
.\.venv\Scripts\python.exe -m customer_matching.analysis
.\data_quality\run-dq.ps1 -Layer master
```

Without `-FailOnQuality`, scans report findings without gating the pipeline.
For a gate, check `$LASTEXITCODE` explicitly before starting the next command.

## Verification

```powershell
.\.venv-dq\Scripts\python.exe -m unittest data_quality.test_run
```

These tests execute Soda against small temporary fixtures, including malformed
data, missing files, invalid contracts, and legitimate empty master outputs.

Soda references: [Core repository and v4 setup](https://github.com/sodadata/soda-core),
[DuckDB connector](https://github.com/sodadata/soda-core/tree/main/soda-duckdb).
