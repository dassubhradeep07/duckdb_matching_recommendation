import argparse
import csv
import json
import logging
from pathlib import Path

import duckdb

from analytics.analytics_pipeline import (
    AUTO_MERGE_CANDIDATES,
    run_analytics,
    validate_manual_review_file,
)
from customer_matching.main import (
    CANDIDATE_SCORES_CSV,
    FIELD_WEIGHTS,
    MATCH_RUN_JSON,
    MATCHED_CSV,
    PROJECT_DIR,
    SOURCE_A_CSV,
    SOURCE_B_CSV,
    configure_logging,
    read_customers,
)
from customer_matching.steward_review import (
    STEWARD_REVIEW_YAML,
    generate_steward_review,
)

DEFAULT_REVIEW_CSV = PROJECT_DIR.parent / "analytics" / "candidate_review.csv"
UNMATCHED_CSV = PROJECT_DIR / "unmatched_customers.csv"


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        return list(csv.DictReader(input_file))


def load_candidate_scores(connection: duckdb.DuckDBPyConnection) -> None:
    columns = (
        "source_index",
        "reference_index",
        "match_score",
        *(f"{field}_similarity" for field in FIELD_WEIGHTS),
    )
    result = connection.execute(
        "SELECT * FROM read_csv(?, header=true, all_varchar=true) LIMIT 0",
        [str(CANDIDATE_SCORES_CSV)],
    )
    found_columns = {column[0] for column in result.description}
    missing_columns = set(columns) - found_columns
    if missing_columns:
        raise ValueError(
            f"{CANDIDATE_SCORES_CSV.name} is missing required columns: "
            f"{', '.join(sorted(missing_columns))}"
        )

    select_columns = [
        "source_index::BIGINT AS source_index",
        "reference_index::BIGINT AS reference_index",
        "match_score::DOUBLE AS match_score",
        *(
            f"try_cast(nullif(\"{field}_similarity\", '') AS DOUBLE) "
            f"AS {field}_similarity"
            for field in FIELD_WEIGHTS
        ),
    ]
    connection.execute(
        f"""
        CREATE TEMP TABLE candidate_scores AS
        SELECT {", ".join(select_columns)}
        FROM read_csv(?, header=true, all_varchar=true)
        """,
        [str(CANDIDATE_SCORES_CSV)],
    )


def selected_index_pairs(
    matched_rows: list[dict[str, str]],
    source_customers: list[dict[str, str]],
    reference_customers: list[dict[str, str]],
) -> set[tuple[int, int]]:
    source_indices = {
        customer["customer_id"]: index
        for index, customer in enumerate(source_customers)
    }
    reference_indices = {
        customer["customer_id"]: index
        for index, customer in enumerate(reference_customers)
    }
    if len(source_indices) != len(source_customers):
        raise ValueError("Source A customer_id values must be unique for analysis.")
    if len(reference_indices) != len(reference_customers):
        raise ValueError("Source B customer_id values must be unique for analysis.")

    selected: set[tuple[int, int]] = set()
    for row in matched_rows:
        source_id = row["source_customer_id"]
        reference_id = row["reference_customer_id"]
        try:
            selected.add((source_indices[source_id], reference_indices[reference_id]))
        except KeyError as error:
            raise ValueError(
                "Matched output refers to a customer ID not found in the input "
                f"datasets: {error.args[0]}"
            ) from error
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze candidate scores created by customer_matching.main."
    )
    parser.add_argument(
        "--review-file",
        type=Path,
        default=DEFAULT_REVIEW_CSV,
        help=f"Candidate review CSV (default: {DEFAULT_REVIEW_CSV}).",
    )
    parser.add_argument(
        "--steward-review-file",
        type=Path,
        default=STEWARD_REVIEW_YAML,
        help=f"Compact steward input YAML (default: {STEWARD_REVIEW_YAML}).",
    )
    parser.add_argument(
        "--auto-merge-threshold",
        type=float,
        default=95.0,
        help=(
            "Score cutoff for the auto-merge candidate report; no records are "
            "merged automatically (default: 95)."
        ),
    )
    args = parser.parse_args()
    if not 0 <= args.auto_merge_threshold <= 100:
        parser.error("--auto-merge-threshold must be between 0 and 100")

    required_files = (
        MATCH_RUN_JSON,
        CANDIDATE_SCORES_CSV,
        MATCHED_CSV,
        UNMATCHED_CSV,
        SOURCE_A_CSV,
        SOURCE_B_CSV,
    )
    missing_files = [path for path in required_files if not path.is_file()]
    if missing_files:
        parser.error(
            "Run customer_matching.main first; required file(s) not found: "
            + ", ".join(str(path) for path in missing_files)
        )

    logger = configure_logging("INFO")
    validate_manual_review_file(args.review_file)
    run_metadata = json.loads(MATCH_RUN_JSON.read_text(encoding="utf-8"))
    matched_rows = read_csv_rows(MATCHED_CSV)
    unmatched_rows = read_csv_rows(UNMATCHED_CSV)

    with duckdb.connect() as connection:
        source_customers = read_customers(connection, SOURCE_A_CSV, "source_a")
        reference_customers = read_customers(connection, SOURCE_B_CSV, "source_b")
        load_candidate_scores(connection)
        candidate_count = connection.execute(
            "SELECT count(*) FROM candidate_scores"
        ).fetchone()[0]
        eligible_count = connection.execute(
            "SELECT count(*) FROM candidate_scores WHERE match_score >= ?",
            [float(run_metadata["threshold"])],
        ).fetchone()[0]
        selected_pairs = selected_index_pairs(
            matched_rows,
            source_customers,
            reference_customers,
        )
        if len(matched_rows) != len(selected_pairs):
            raise ValueError("Matched output contains duplicate selected customer pairs.")

        result = run_analytics(
            connection,
            run_id=str(run_metadata["run_id"]),
            threshold=float(run_metadata["threshold"]),
            source_a_count=len(source_customers),
            source_b_count=len(reference_customers),
            candidate_count=int(candidate_count),
            eligible_count=int(eligible_count),
            matched_count=len(matched_rows),
            unmatched_count=len(unmatched_rows),
            elapsed_seconds=float(run_metadata["elapsed_seconds"]),
            field_weights=FIELD_WEIGHTS,
            source_customers=source_customers,
            reference_customers=reference_customers,
            selected_pairs=selected_pairs,
            review_file=args.review_file,
            auto_merge_threshold=args.auto_merge_threshold,
            steward_review_file=args.steward_review_file,
        )

    steward_row_count = generate_steward_review(
        output_path=args.steward_review_file,
    )
    logger.info(
        "Analysis completed: %d candidates; recommendation threshold %.2f (%s)",
        candidate_count,
        result["recommended_threshold"],
        result["model_type"],
    )
    logger.info("Candidate review CSV: %s", result["review_file"])
    logger.info(
        "Auto-merge candidate report: %s (%d rows)",
        AUTO_MERGE_CANDIDATES,
        result["auto_merge_candidate_count"],
    )
    logger.info("Recommendation file: %s", result["recommendation_file"])
    logger.info(
        "Steward review queue: %s (%d candidate rows)",
        args.steward_review_file,
        steward_row_count,
    )


if __name__ == "__main__":
    main()
