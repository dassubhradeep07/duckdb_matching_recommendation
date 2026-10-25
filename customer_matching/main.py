import argparse
import csv
import json
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Iterator

import duckdb
import psutil

from analytics.analytics_pipeline import (
    append_performance_history,
    bootstrap_history,
)

PROJECT_DIR = Path(__file__).resolve().parent
SOURCE_A_CSV = PROJECT_DIR / "data" / "source_a.csv"
SOURCE_B_CSV = PROJECT_DIR / "data" / "source_b.csv"
MATCHED_CSV = PROJECT_DIR / "matched_customers.csv"
UNMATCHED_CSV = PROJECT_DIR / "unmatched_customers.csv"
SCORE_SUMMARY_CSV = PROJECT_DIR / "candidate_score_summary.csv"
METRICS_CSV = PROJECT_DIR / "performance_metrics.csv"
LOG_FILE = PROJECT_DIR / "customer_matching.log"
CANDIDATE_SCORES_CSV = PROJECT_DIR / "candidate_scores.csv"
MATCH_RUN_JSON = PROJECT_DIR / "match_run.json"

CUSTOMER_FIELDS = (
    "customer_id",
    "customer_name",
    "email",
    "phone",
    "address",
    "city",
    "state",
    "postal_code",
    "country",
)
FIELD_WEIGHTS = {
    "customer_name": 0.30,
    "email": 0.25,
    "phone": 0.20,
    "address": 0.08,
    "city": 0.05,
    "state": 0.03,
    "postal_code": 0.06,
    "country": 0.03,
}


@dataclass(frozen=True)
class ScoredPair:
    source_index: int
    reference_index: int
    score: float


@dataclass(frozen=True)
class MemorySnapshot:
    process_rss_bytes: int
    duckdb_memory_bytes: int
    duckdb_temporary_bytes: int


def configure_logging(log_level: str) -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
        force=True,
    )
    logger = logging.getLogger("duckdb_customer_matching")
    logger.debug(
        "Debug logging is enabled. DEBUG lines explain each step; INFO lines "
        "show the run summary and resource measurements."
    )
    return logger


def memory_snapshot(
    connection: duckdb.DuckDBPyConnection | None,
) -> MemorySnapshot:
    process_rss_bytes = psutil.Process(os.getpid()).memory_info().rss
    if connection is None:
        return MemorySnapshot(process_rss_bytes, 0, 0)
    memory_rows = connection.execute(
        """
        SELECT tag, memory_usage_bytes, temporary_storage_bytes
        FROM duckdb_memory()
        ORDER BY tag
        """
    ).fetchall()
    return MemorySnapshot(
        process_rss_bytes,
        sum(row[1] for row in memory_rows),
        sum(row[2] for row in memory_rows),
    )


@contextmanager
def measure_stage(
    logger: logging.Logger,
    stage: str,
    metrics: list[dict[str, object]],
    connection: duckdb.DuckDBPyConnection | None = None,
) -> Iterator[None]:
    logger.debug(
        "Starting operation '%s'. The next timing and memory line measures "
        "this operation only.",
        stage,
    )
    before = memory_snapshot(connection)
    sampled_peak_rss = before.process_rss_bytes
    stop_sampling = threading.Event()

    def sample_process_memory() -> None:
        nonlocal sampled_peak_rss
        process = psutil.Process(os.getpid())
        while not stop_sampling.wait(0.02):
            sampled_peak_rss = max(sampled_peak_rss, process.memory_info().rss)

    sampler = threading.Thread(target=sample_process_memory, daemon=True)
    started_at = perf_counter()
    sampler.start()
    try:
        yield
    finally:
        stop_sampling.set()
        sampler.join()
        elapsed = perf_counter() - started_at
        after = memory_snapshot(connection)
        process_info = psutil.Process(os.getpid()).memory_info()
        sampled_peak_rss = max(sampled_peak_rss, after.process_rss_bytes)
        peak_working_set = getattr(process_info, "peak_wset", sampled_peak_rss)
        mib = 1024 * 1024
        logger.info(
            "Stage '%s': %.3f sec; process RSS %0.2f -> %0.2f MiB "
            "(delta %+.2f MiB, sampled stage peak %0.2f MiB, "
            "process high-water %0.2f MiB); DuckDB live memory %0.2f -> %0.2f MiB "
            "(delta %+.2f MiB), temporary storage %0.2f -> %0.2f MiB",
            stage,
            elapsed,
            before.process_rss_bytes / mib,
            after.process_rss_bytes / mib,
            (after.process_rss_bytes - before.process_rss_bytes) / mib,
            sampled_peak_rss / mib,
            peak_working_set / mib,
            before.duckdb_memory_bytes / mib,
            after.duckdb_memory_bytes / mib,
            (after.duckdb_memory_bytes - before.duckdb_memory_bytes) / mib,
            before.duckdb_temporary_bytes / mib,
            after.duckdb_temporary_bytes / mib,
        )
        logger.debug(
            "Completed operation '%s'. RSS means total Python process memory; "
            "DuckDB live memory is DuckDB's internal allocator usage. "
            "The stage peak is sampled every 20 ms.",
            stage,
        )
        metrics.append(
            {
                "operation": stage,
                "duration_seconds": round(elapsed, 6),
                "process_rss_before_mib": round(before.process_rss_bytes / mib, 3),
                "process_rss_after_mib": round(after.process_rss_bytes / mib, 3),
                "process_rss_delta_mib": round(
                    (after.process_rss_bytes - before.process_rss_bytes) / mib, 3
                ),
                "process_rss_peak_sampled_mib": round(sampled_peak_rss / mib, 3),
                "process_peak_working_set_mib": round(peak_working_set / mib, 3),
                "duckdb_memory_before_mib": round(
                    before.duckdb_memory_bytes / mib, 3
                ),
                "duckdb_memory_after_mib": round(
                    after.duckdb_memory_bytes / mib, 3
                ),
                "duckdb_temp_before_mib": round(
                    before.duckdb_temporary_bytes / mib, 3
                ),
                "duckdb_temp_after_mib": round(
                    after.duckdb_temporary_bytes / mib, 3
                ),
            }
        )


def read_customers(
    connection: duckdb.DuckDBPyConnection, csv_path: Path, table_name: str
) -> list[dict[str, str]]:
    logger = logging.getLogger("duckdb_customer_matching")
    logger.debug(
        "Validating columns in %s before loading it into temporary DuckDB "
        "table '%s'.",
        csv_path.name,
        table_name,
    )
    result = connection.execute(
        "SELECT * FROM read_csv(?, header=true, all_varchar=true) LIMIT 0",
        [str(csv_path)],
    )
    column_names = [column[0] for column in result.description]
    missing_fields = set(CUSTOMER_FIELDS) - set(column_names)
    if missing_fields:
        logger.error(
            "Input file %s is missing required columns: %s",
            csv_path,
            ", ".join(sorted(missing_fields)),
        )
        raise ValueError(
            f"{csv_path.name} is missing required columns: "
            f"{', '.join(sorted(missing_fields))}"
        )
    selected_columns = ", ".join(f'"{field}"' for field in CUSTOMER_FIELDS)
    logger.debug(
        "CSV columns found: %s. Required customer columns: %s.",
        ", ".join(column_names),
        ", ".join(CUSTOMER_FIELDS),
    )
    logger.debug(
        "DuckDB load query for '%s': CREATE TEMP TABLE %s AS SELECT "
        "row_number() OVER () - 1, %s FROM read_csv(?, header=true, "
        "all_varchar=true). CSV values are initially read as text.",
        csv_path.name,
        table_name,
        selected_columns,
    )
    connection.execute(
        f"""
        CREATE TEMP TABLE {table_name} AS
        SELECT
            row_number() OVER () - 1 AS record_index,
            {selected_columns}
        FROM read_csv(?, header=true, all_varchar=true)
        """,
        [str(csv_path)],
    )
    result = connection.execute(
        f"SELECT {selected_columns} FROM {table_name} ORDER BY record_index"
    )
    customers = [
        dict(zip(CUSTOMER_FIELDS, row))
        for row in result.fetchall()
    ]
    logger.debug(
        "Loaded %d rows from %s into '%s'; returned %d customer records "
        "to Python for output generation.",
        len(customers),
        csv_path.name,
        table_name,
        len(customers),
    )
    return customers


def similarity_sql(field: str) -> str:
    if field == "phone":
        normalization = "regexp_replace(lower(trim(%s)), '[^0-9]', '', 'g')"
    else:
        normalization = (
            "regexp_replace(strip_accents(lower(trim(%s))), '[^a-z0-9]', '', 'g')"
        )
    source_value = normalization % f"coalesce(a.{field}, '')"
    reference_value = normalization % f"coalesce(b.{field}, '')"
    return f"""
        CASE
            WHEN {source_value} = '' OR {reference_value} = '' THEN NULL
            ELSE jaro_winkler_similarity({source_value}, {reference_value})
        END
    """


def create_candidate_scores(connection: duckdb.DuckDBPyConnection) -> None:
    logger = logging.getLogger("duckdb_customer_matching")
    logger.debug(
        "Candidate-generation explanation: CROSS JOIN forms pairs only "
        "inside equal, non-empty normalized postal-code blocks. Each pair "
        "is scored using Jaro-Winkler similarity; missing field values are "
        "excluded from the weighted average."
    )
    logger.debug(
        "Similarity weights: %s",
        ", ".join(f"{field}={weight:.0%}" for field, weight in FIELD_WEIGHTS.items()),
    )
    logger.debug(
        "DuckDB scoring query creates candidate_scores, computes field "
        "similarities and a weighted match_score, then ROW_NUMBER() ranks "
        "candidates for each source and reference customer."
    )
    similarity_columns = ", ".join(
        f"{similarity_sql(field)} AS {field}_similarity"
        for field in FIELD_WEIGHTS
    )
    weighted_score = " + ".join(
        f"coalesce({field}_similarity, 0) * {weight}"
        for field, weight in FIELD_WEIGHTS.items()
    )
    available_weight = " + ".join(
        f"CASE WHEN {field}_similarity IS NOT NULL THEN {weight} ELSE 0 END"
        for field, weight in FIELD_WEIGHTS.items()
    )
    connection.execute(
        f"""
        CREATE TEMP TABLE candidate_scores AS
        WITH field_scores AS (
            SELECT
                a.record_index AS source_index,
                b.record_index AS reference_index,
                {similarity_columns}
            FROM source_a AS a
            CROSS JOIN source_b AS b
            WHERE regexp_replace(
                coalesce(a.postal_code, ''), '[^a-zA-Z0-9]', '', 'g'
            ) = regexp_replace(
                coalesce(b.postal_code, ''), '[^a-zA-Z0-9]', '', 'g'
            )
            AND trim(coalesce(a.postal_code, '')) <> ''
        ),
        weighted_scores AS (
            SELECT
                *,
                CASE
                    WHEN ({available_weight}) = 0 THEN 0
                    ELSE ({weighted_score}) / ({available_weight}) * 100
                END AS match_score
            FROM field_scores
        )
        SELECT
            *,
            row_number() OVER (
                PARTITION BY source_index
                ORDER BY match_score DESC, reference_index
            ) AS source_rank,
            row_number() OVER (
                PARTITION BY reference_index
                ORDER BY match_score DESC, source_index
            ) AS reference_rank
        FROM weighted_scores
        """
    )
    logger.debug(
        "Candidate score table is ready with the columns source_index, "
        "reference_index, per-field similarities, match_score, source_rank, "
        "and reference_rank."
    )


def read_scored_pairs(
    connection: duckdb.DuckDBPyConnection, threshold: float | None = None
) -> list[ScoredPair]:
    query = """
        SELECT source_index, reference_index, match_score
        FROM candidate_scores
    """
    parameters: list[float] = []
    if threshold is not None:
        query += " WHERE match_score >= ?"
        parameters.append(threshold)
    query += " ORDER BY match_score DESC, source_rank, reference_rank"
    logging.getLogger("duckdb_customer_matching").debug(
        "Candidate query: %s; parameter values: %s. This fetches only "
        "eligible candidates, not the complete score table.",
        " ".join(query.split()),
        parameters,
    )
    pairs = [
        ScoredPair(int(source_index), int(reference_index), float(score))
        for source_index, reference_index, score in connection.execute(
            query, parameters
        ).fetchall()
    ]
    logger = logging.getLogger("duckdb_customer_matching")
    logger.debug("Fetched %d candidate pairs into Python.", len(pairs))
    for pair in pairs[:5]:
        logger.debug(
            "Candidate preview uses zero-based row indexes (not customer IDs): "
            "source row %d, reference row %d, score %.2f.",
            pair.source_index,
            pair.reference_index,
            pair.score,
        )
    return pairs


def read_best_candidate_scores(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    index_column: str,
) -> list[float]:
    logging.getLogger("duckdb_customer_matching").debug(
        "Best-score aggregation: group candidate_scores by %s, calculate "
        "MAX(match_score), then LEFT JOIN it to every input record. The left "
        "join preserves records with no candidate; their score becomes 0.",
        index_column,
    )
    results = connection.execute(
        f"""
        SELECT records.record_index, coalesce(scores.best_score, 0)
        FROM {table_name} AS records
        LEFT JOIN (
            SELECT {index_column} AS record_index, max(match_score) AS best_score
            FROM candidate_scores
            GROUP BY {index_column}
        ) AS scores USING (record_index)
        ORDER BY records.record_index
        """
    ).fetchall()
    scores = [float(best_score) for _, best_score in results]
    logging.getLogger("duckdb_customer_matching").debug(
        "Best-score aggregation returned one score for each of %d records in %s.",
        len(scores),
        table_name,
    )
    return scores


def write_score_summary(
    connection: duckdb.DuckDBPyConnection, threshold: float
) -> int:
    logging.getLogger("duckdb_customer_matching").debug(
        "Score summary groups candidate scores into four bands, uses COUNT "
        "for pair totals, SUM(CASE...) for candidates at threshold %.2f, and "
        "AVG/MAX for score statistics.",
        threshold,
    )
    result = connection.execute(
        """
        WITH classified_pairs AS (
            SELECT
                match_score,
                CASE
                    WHEN match_score = 100 THEN '100 exact'
                    WHEN match_score >= 80 THEN '80-99.99 fuzzy'
                    WHEN match_score >= 40 THEN '40-79.99 review'
                    ELSE '0-39.99 weak'
                END AS score_band
            FROM candidate_scores
        )
        SELECT
            score_band,
            count(*) AS candidate_pair_count,
            sum(CASE WHEN match_score >= ? THEN 1 ELSE 0 END)
                AS eligible_pair_count,
            round(avg(match_score), 2) AS average_score,
            round(max(match_score), 2) AS highest_score
        FROM classified_pairs
        GROUP BY score_band
        ORDER BY CASE score_band
            WHEN '100 exact' THEN 1
            WHEN '80-99.99 fuzzy' THEN 2
            WHEN '40-79.99 review' THEN 3
            ELSE 4
        END
        """,
        [threshold],
    )
    summary_rows = [
        dict(zip([column[0] for column in result.description], row))
        for row in result.fetchall()
    ]
    write_csv(
        SCORE_SUMMARY_CSV,
        [column[0] for column in result.description],
        summary_rows,
    )
    logging.getLogger("duckdb_customer_matching").debug(
        "Wrote %d score-band summary rows to %s: %s",
        len(summary_rows),
        SCORE_SUMMARY_CSV,
        summary_rows,
    )
    return connection.execute("SELECT count(*) FROM candidate_scores").fetchone()[0]


def write_candidate_scores(connection: duckdb.DuckDBPyConnection) -> None:
    fieldnames = [
        "source_index",
        "reference_index",
        "match_score",
        *(
            f"{field}_similarity"
            for field in FIELD_WEIGHTS
        ),
    ]
    rows = connection.execute(
        f"""
        SELECT {", ".join(fieldnames)}
        FROM candidate_scores
        ORDER BY source_index, reference_index
        """
    ).fetchall()
    write_csv(
        CANDIDATE_SCORES_CSV,
        fieldnames,
        [dict(zip(fieldnames, row)) for row in rows],
    )


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    logger = logging.getLogger("duckdb_customer_matching")
    logger.debug(
        "Writing %d rows and %d columns to %s.",
        len(rows),
        len(fieldnames),
        path,
    )
    with path.open("w", newline="", encoding="utf-8-sig") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    logger.debug(
        "Finished writing %s (%d bytes).",
        path,
        path.stat().st_size,
    )


def make_output_rows(
    source_customers: list[dict[str, str]],
    reference_customers: list[dict[str, str]],
    eligible_pairs: list[ScoredPair],
    best_source_candidate_score: list[float],
    best_reference_candidate_score: list[float],
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    set[tuple[int, int]],
]:
    matched_source: set[int] = set()
    matched_reference: set[int] = set()
    selected_pairs: set[tuple[int, int]] = set()
    matched_rows: list[dict[str, object]] = []
    skipped_conflicts = 0

    for pair in eligible_pairs:
        if (
            pair.source_index in matched_source
            or pair.reference_index in matched_reference
        ):
            skipped_conflicts += 1
            continue
        matched_source.add(pair.source_index)
        matched_reference.add(pair.reference_index)
        selected_pairs.add((pair.source_index, pair.reference_index))
        row: dict[str, object] = {"match_score": round(pair.score, 2)}
        row.update(
            {
                f"source_{field}": value
                for field, value in source_customers[pair.source_index].items()
            }
        )
        row.update(
            {
                f"reference_{field}": value
                for field, value in reference_customers[pair.reference_index].items()
            }
        )
        matched_rows.append(row)

    logging.getLogger("duckdb_customer_matching").debug(
        "Greedy one-to-one rule: process pairs from highest score downward; "
        "accept a pair only if neither customer was matched already. "
        "Accepted %d; skipped %d pairs because one side was already assigned.",
        len(matched_rows),
        skipped_conflicts,
    )

    unmatched_rows: list[dict[str, object]] = []
    for index, customer in enumerate(source_customers):
        if index not in matched_source:
            unmatched_rows.append(
                {
                    "source": "source_a",
                    "best_candidate_score": round(
                        best_source_candidate_score[index], 2
                    ),
                    **customer,
                }
            )
    for index, customer in enumerate(reference_customers):
        if index not in matched_reference:
            unmatched_rows.append(
                {
                    "source": "source_b",
                    "best_candidate_score": round(
                        best_reference_candidate_score[index], 2
                    ),
                    **customer,
                }
            )

    logging.getLogger("duckdb_customer_matching").debug(
        "Output preparation complete: %d matched rows and %d unmatched rows. "
        "Unmatched best_candidate_score is informational; it may be below "
        "the threshold or may point to a candidate assigned elsewhere.",
        len(matched_rows),
        len(unmatched_rows),
    )
    return matched_rows, unmatched_rows, selected_pairs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Match customer records from two CSV files using DuckDB and Python."
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=75.0,
        help="Minimum match score from 0 to 100 (default: 75).",
    )
    parser.add_argument(
        "--log-level",
        choices=("INFO", "DEBUG"),
        default="INFO",
        help="Use DEBUG to include beginner-friendly step-by-step explanations.",
    )
    args = parser.parse_args()
    if not 0 <= args.threshold <= 100:
        parser.error("--threshold must be between 0 and 100")
    source_a_csv = SOURCE_A_CSV
    source_b_csv = SOURCE_B_CSV
    for csv_path in (source_a_csv, source_b_csv):
        if not csv_path.is_file():
            parser.error(f"Input CSV not found: {csv_path}")

    metrics: list[dict[str, object]] = []
    logger = configure_logging(args.log_level)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    logger.info("Starting DuckDB customer matching")
    logger.info("Run ID: %s", run_id)
    logger.info("Log level: %s", args.log_level)
    logger.info("DuckDB version: %s", duckdb.__version__)
    logger.info("Source A: %s (%d bytes)", source_a_csv, source_a_csv.stat().st_size)
    logger.info("Source B: %s (%d bytes)", source_b_csv, source_b_csv.stat().st_size)
    logger.info("Match threshold: %.2f", args.threshold)
    logger.info(
        "Candidate blocking: normalized postal code equality; an unblocked "
        "10,000 x 10,000 comparison would create 100,000,000 pairs"
    )
    logger.debug(
        "Run mode: 10,000-row customer data. Threshold: %.2f out of 100. "
        "Customer ID is retained in outputs but is not included in the score.",
        args.threshold,
    )
    logger.debug(
        "Output paths: matches=%s; unmatched=%s; score summary=%s; "
        "performance metrics=%s; log=%s",
        MATCHED_CSV,
        UNMATCHED_CSV,
        SCORE_SUMMARY_CSV,
        METRICS_CSV,
        LOG_FILE,
    )
    logger.debug(
        "Memory guide: RSS is memory used by the complete Python process. "
        "DuckDB memory is only DuckDB's internal live allocator usage. "
        "Temporary storage is DuckDB spill/storage usage. A positive or "
        "negative RSS delta is a before/after difference, not memory owned "
        "only by the operation."
    )

    run_started_at = perf_counter()
    try:
        bootstrap_history()
        with measure_stage(logger, "Open DuckDB connection", metrics):
            connection = duckdb.connect()
        with connection:
            with measure_stage(logger, "Load source A CSV", metrics, connection):
                source_customers = read_customers(
                    connection, source_a_csv, "source_a"
                )
            logger.info(
                "Loaded %d source A customer rows",
                len(source_customers),
            )
            with measure_stage(logger, "Load source B CSV", metrics, connection):
                reference_customers = read_customers(
                    connection, source_b_csv, "source_b"
                )
            logger.info(
                "Loaded %d source B customer rows",
                len(reference_customers),
            )

            with measure_stage(logger, "Score and rank blocked candidate pairs", metrics, connection):
                create_candidate_scores(connection)
                candidate_count = connection.execute(
                    "SELECT count(*) FROM candidate_scores"
                ).fetchone()[0]
                write_candidate_scores(connection)
            logger.info(
                "DuckDB scored %d pairs after postal-code blocking "
                "(compared with 100,000,000 unblocked pairs)",
                candidate_count,
            )

            with measure_stage(logger, "Filter candidates by threshold", metrics, connection):
                eligible_pairs = read_scored_pairs(connection, args.threshold)
            logger.info(
                "%d candidates meet the %.2f threshold",
                len(eligible_pairs),
                args.threshold,
            )
            if eligible_pairs:
                logger.debug(
                    "Eligible score range: lowest %.2f, highest %.2f.",
                    min(pair.score for pair in eligible_pairs),
                    max(pair.score for pair in eligible_pairs),
                )
            else:
                logger.debug(
                    "No candidate reached the threshold. All input records "
                    "will be written as unmatched."
                )

            with measure_stage(logger, "Aggregate score bands", metrics, connection):
                write_score_summary(connection, args.threshold)
            with measure_stage(logger, "Aggregate best candidate scores", metrics, connection):
                best_source_candidate_score = read_best_candidate_scores(
                    connection, "source_a", "source_index"
                )
                best_reference_candidate_score = read_best_candidate_scores(
                    connection, "source_b", "reference_index"
                )

            with measure_stage(logger, "Greedy one-to-one assignment", metrics, connection):
                matched_rows, unmatched_rows, selected_pairs = make_output_rows(
                    source_customers,
                    reference_customers,
                    eligible_pairs,
                    best_source_candidate_score,
                    best_reference_candidate_score,
                )

            matched_fields = (
                ["match_score"]
                + [f"source_{field}" for field in CUSTOMER_FIELDS]
                + [f"reference_{field}" for field in CUSTOMER_FIELDS]
            )
            unmatched_fields = ["source", "best_candidate_score", *CUSTOMER_FIELDS]
            with measure_stage(logger, "Write matched and unmatched CSVs", metrics, connection):
                write_csv(MATCHED_CSV, matched_fields, matched_rows)
                write_csv(UNMATCHED_CSV, unmatched_fields, unmatched_rows)

        logger.info(
            "Matching completed: %d matches and %d unmatched records",
            len(matched_rows),
            len(unmatched_rows),
        )
        logger.info("Matched output: %s", MATCHED_CSV)
        logger.info("Unmatched output: %s", UNMATCHED_CSV)
        logger.info("Candidate score summary: %s", SCORE_SUMMARY_CSV)
        write_csv(
            METRICS_CSV,
            list(metrics[0]),
            metrics,
        )
        append_performance_history(run_id, metrics)
        MATCH_RUN_JSON.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "threshold": args.threshold,
                    "source_a_count": len(source_customers),
                    "source_b_count": len(reference_customers),
                    "candidate_count": int(candidate_count),
                    "eligible_count": len(eligible_pairs),
                    "matched_count": len(matched_rows),
                    "unmatched_count": len(unmatched_rows),
                    "elapsed_seconds": round(perf_counter() - run_started_at, 6),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        logger.info("Per-operation memory and timing report: %s", METRICS_CSV)
        logger.info("Candidate scores for analysis: %s", CANDIDATE_SCORES_CSV)
        logger.info(
            "End-to-end matching run completed in %.3f seconds",
            perf_counter() - run_started_at,
        )
        logger.info("Full log written to: %s", LOG_FILE)
    except Exception:
        logger.exception("Customer matching failed")
        raise


if __name__ == "__main__":
    main()
