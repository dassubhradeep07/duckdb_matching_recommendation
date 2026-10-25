# Customer matching pipeline

This folder contains the matching program, two 10,000-row customer input
datasets, and generated matching reports.
The only input datasets are `data/source_a.csv` and `data/source_b.csv`.
Run `.\.venv\Scripts\python.exe .\customer_matching\generate_data.py` from
the project root to regenerate them.

## Run matching, then analysis

```powershell
Set-Location C:\Users\SUBHRADEEP\duckdb-test
.\.venv\Scripts\python.exe -m customer_matching.main
.\.venv\Scripts\python.exe -m customer_matching.analysis
```

Run the commands in order from the project root. Matching saves its
intermediate `candidate_scores.csv` and `match_run.json`; analysis reads those
files to create candidate-review and recommendation reports, then refreshes
`human-review/steward_review.yaml` with up to 100 pending candidates.
Previously reviewed rows are retained so decisions are not lost. If the input
datasets change, rerun matching before analysis.

Stewards should edit only `steward_decision` (`approve`, `reject`, or blank)
and `steward_comment` in the YAML. The next analysis run imports these decisions, updates
recommendations, and regenerates the compact queue while preserving the
steward's existing decisions. To regenerate the queue separately:

```powershell
.\.venv\Scripts\python.exe .\human-review\generate.py --generate
```

## What the program does

1. Loads and validates the two customer CSV files with DuckDB.
2. Builds candidate pairs only when their postal codes match.
3. Scores available fields with weighted Jaro-Winkler similarity. Missing
   values do not contribute to the weighted average.
4. Selects one-to-one matches in descending score order at or above the
   threshold (75 by default).
5. Saves matched and unmatched results and sends candidate scores to the
   analytics pipeline.
6. Produces a candidate-review CSV and an auto-merge candidate report. The
   latter only lists candidates; it never merges or changes input records.

See [matching.md](matching.md) for the scoring rules and the match-quality
scenarios built into the synthetic datasets.

The greedy one-to-one selection is simple and deterministic, but is not
guaranteed to be the globally optimal assignment.

## Manual review loop

After a run, open `analytics/candidate_review.csv`. For each candidate you
review, edit `review_decision` to `approved` or `rejected`, and optionally add
a `review_comment`. Keep `candidate_key` and other generated columns intact.
Save the same CSV, then rerun the command. The program matches decisions back
to candidates by a hash of both complete customer records. If either record
changes, that old decision is not applied to the changed pair.

Use `--review-file <path>` to read and update a different review CSV.
Invalid decisions, missing keys, or duplicate keys produce an error.

With at least 20 labels (including at least five in each decision class), a
logistic-regression model learns from the reviewer labels and ranks unreviewed
candidates. The model approval score is not a calibrated probability or proof
of accuracy. Until that minimum is met, the system uses score clusters when
there is enough candidate data and otherwise recommends manual review. A
model recommendation is advisory and never changes the matching decision.

## Reports

- `matched_customers.csv`: selected pairs and match scores.
- `unmatched_customers.csv`: not-selected records and their best candidate
  score.
- `auto_merge_candidates.csv`: selected one-to-one matches at or above 95
  (configurable with `--auto-merge-threshold`) excluding reviewer-rejected
  pairs. These are candidates for a separate human-approved merge process;
  this program does not perform a merge.
- `candidate_score_summary.csv`: DuckDB candidate counts and score bands.
- `performance_metrics.csv`: timings and memory per operation.
- `customer_matching.log`: run settings, step summaries, and errors.
- `../analytics/candidate_review.csv`: all candidates, scores, clusters,
  recommendations, editable decisions, and comments.
- `../analytics/history/`: append-only aggregate run, rule, pattern,
  performance, and recommendation history.

Use `--threshold 85`, `--auto-merge-threshold 98`, or `--log-level DEBUG` to
change the match cutoff, candidate-report cutoff, or log detail.
