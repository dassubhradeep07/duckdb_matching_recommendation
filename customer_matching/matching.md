# Customer matching rules

## Run in two steps

From the project root, run matching first and analysis second:

```powershell
Set-Location C:\Users\SUBHRADEEP\duckdb-test
.\.venv\Scripts\python.exe -m customer_matching.main
.\.venv\Scripts\python.exe -m customer_matching.analysis
```

The match step writes `candidate_scores.csv` and `match_run.json` for the
analysis step. Rerun matching whenever either input dataset changes.
Analysis also refreshes `human-review/steward_review.yaml` with a stratified
queue of up to 100 pending candidates. Previously reviewed rows remain in the
file so their decisions are not lost.

Stewards edit only `steward_decision` (`approve`, `reject`, or blank) and
`steward_comment`. On the next analysis run, these labels feed the existing
recommendation model and the compact queue is regenerated with the decisions
preserved. At least 20 labels, including at least five approvals and five
rejections, are required before the supervised model can train. To generate
the queue manually after analysis, run:

```powershell
.\.venv\Scripts\python.exe .\human-review\generate.py --generate
```

## Candidate generation

The matcher only compares rows whose postal codes are equal after removing
non-alphanumeric characters. A pair with a different or missing postal code
is not a candidate, regardless of its other fields.

## Field normalization and scoring

The matcher compares customer name, email, phone, address, city, state,
postal code, and country. It uses Jaro-Winkler similarity for each available
field, with these weights:

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

Phone values are lowercased and stripped to digits. Other fields are
lowercased, stripped of accents, and stripped to alphanumeric characters.
Missing values are left out of the weighted average; the remaining weights
are renormalized to 100%. `customer_id` is retained in the reports but is
not scored.

## Score bands and match decision

The candidate score is the weighted average of its available field
similarities, expressed from 0 to 100. The score summary groups candidate
pairs into:

| Score | Interpretation |
| --- | --- |
| 100 | Exact after normalization |
| 80–99.99 | Fuzzy, strong similarity |
| 40–79.99 | Review range |
| 0–39.99 | Weak similarity |

By default, only candidates scoring at least 75 are eligible for matching.
Eligible candidates are considered from highest to lowest score and selected
greedily, with each source and reference row used at most once. This
deterministic one-to-one rule is not a globally optimal assignment.
Unselected rows are written to `unmatched_customers.csv` with their best
candidate score; a score of 0 means no candidate was generated for that row.

## Synthetic dataset scenarios

`generate_data.py` deterministically creates 10,000 rows in each input file.
The group scenarios repeat every twenty postal-code groups:

| Share | Scenario |
| ---: | --- |
| 5% | Exact records |
| 25% | One-character variation in name, email, phone, or address |
| 35% | Larger variations across those four fields, intended for the review range |
| 25% | Strongly dissimilar values in those four fields, intended to score below 40 |
| 10% | Reference postal code changed to a code absent from source A, producing no candidates |

The input files are shuffled independently on the reference side, and the
random seed is fixed so the scenarios can be regenerated reproducibly.
Observed scores can vary with candidate competition within a postal-code
block; the scenario percentages describe generated records, not guaranteed
match outcomes.
