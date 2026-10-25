# DuckDB customer matching and review analytics

The project reads two customer CSV datasets (10,000 rows each), scores
possible customer pairs with DuckDB, creates one-to-one match suggestions,
and captures manual review to improve future recommendations.

## Run

From `C:\Users\SUBHRADEEP\duckdb-test`, run matching and analysis separately,
in this order:

```powershell
.\.venv\Scripts\python.exe -m customer_matching.main
.\.venv\Scripts\python.exe -m customer_matching.analysis
```

Matching writes the match reports and intermediate candidate scores.
Analysis uses those intermediate files to produce review recommendations and
analytics, then generates a compact, 100-candidate steward queue at
`human-review/steward_review.yaml`. Previously reviewed rows are retained in
addition to the pending queue so steward decisions are not lost. If either
input CSV changes, rerun matching before analysis.

Stewards should edit only `steward_decision` (`approve`, `reject`, or blank)
and `steward_comment` in that YAML file. After entering decisions, optionally
fill a standard comment for each decided row that has a blank comment:

```powershell
.\.venv\Scripts\python.exe .\human-review\fill_standard_notes.py
```

Then rerun analysis to import the steward labels and refresh recommendations;
rerun matching first only if either input dataset changed:

```powershell
.\.venv\Scripts\python.exe -m customer_matching.analysis
```

The input files are:

- `customer_matching/data/source_a.csv`
- `customer_matching/data/source_b.csv`

The default match threshold is 75. Use `--threshold 85` to change it.
Candidates with a selected one-to-one match and a score of at least 95 are
listed as auto-merge candidates by default. This is a report only; the program
never changes customer records or merges them.

## Record manual review

The run creates `analytics/candidate_review.csv`, with a row for every
candidate pair. Open the file in a spreadsheet program and edit only:

- `review_decision`: enter `approved` or `rejected`; leave undecided rows blank.
- `review_comment`: add the reason for the decision.

Save the same CSV and rerun the matching command. Decisions and comments are
kept by a stable candidate key when the source records have not changed.
Candidate rows are regenerated with refreshed scores and recommendations.
Use `--review-file C:\path\to\candidate_review.csv` to choose another review
CSV location.

The supervised recommendation model is trained after it has at least 20
reviewed candidates, including at least five approved and five rejected
examples. Until then, candidates are ranked by score clusters and remain
subject to human review. A trained model recommends which unreviewed
candidates to prioritize; it does not auto-approve or change the match
threshold. The `model_approval_score` column is not a calibrated probability
or a measure of proven accuracy.

## Main outputs

- `customer_matching/matched_customers.csv`: selected one-to-one pairs and
  their score.
- `customer_matching/unmatched_customers.csv`: records not selected, with
  their best candidate score where one exists.
- `customer_matching/auto_merge_candidates.csv`: selected pairs at or above
  the auto-merge threshold, excluding reviewer-rejected pairs. This is a
  review queue, not an automatic merge.
- `analytics/candidate_review.csv`: scores, clusters, recommendations, review
  decisions, and comments for every generated candidate.
- `analytics/recommendation.csv`: current run's recommendation and rationale.
- `analytics/history/`: run, field-rule, score-pattern, performance, and
  recommendation history.

Use `--auto-merge-threshold 98` to raise the candidate-list score cutoff, or
`--log-level DEBUG` for step-by-step explanations. Logs are written to
`customer_matching/customer_matching.log`.

Regenerate both deterministic 10,000-row synthetic inputs with:

```powershell
.\.venv\Scripts\python.exe .\customer_matching\generate_data.py
```

The supplied data is synthetic. Review labels should be treated as human
judgment for this data; model recommendations are not proof of identity.
