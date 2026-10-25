import csv
import hashlib
import json
import logging
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import yaml
from sklearn.linear_model import LogisticRegression
from sklearn.cluster import KMeans


PROJECT_DIR = Path(__file__).resolve().parents[1]
CUSTOMER_MATCHING_DIR = PROJECT_DIR / "customer_matching"
HISTORY_DIR = Path(__file__).resolve().parent / "history"
LEGACY_SCORE_SUMMARY = CUSTOMER_MATCHING_DIR / "candidate_score_summary.csv"
LEGACY_METRICS = CUSTOMER_MATCHING_DIR / "performance_metrics.csv"
LEGACY_MATCHED = CUSTOMER_MATCHING_DIR / "matched_customers.csv"
LEGACY_UNMATCHED = CUSTOMER_MATCHING_DIR / "unmatched_customers.csv"
RUN_HISTORY = HISTORY_DIR / "run_history.csv"
RULE_HISTORY = HISTORY_DIR / "rule_history.csv"
PATTERN_HISTORY = HISTORY_DIR / "pattern_history.csv"
PERFORMANCE_HISTORY = HISTORY_DIR / "performance_history.csv"
RECOMMENDATION_HISTORY = HISTORY_DIR / "recommendation_history.csv"
CURRENT_RECOMMENDATION = Path(__file__).resolve().parent / "recommendation.csv"
CANDIDATE_RECOMMENDATIONS = (
    Path(__file__).resolve().parent / "candidate_review.csv"
)
AUTO_MERGE_CANDIDATES = (
    CUSTOMER_MATCHING_DIR / "auto_merge_candidates.csv"
)
MINIMUM_CLUSTER_CANDIDATES = 30
MINIMUM_REVIEW_LABELS = 20
MINIMUM_LABELS_PER_CLASS = 5
SIMILARITY_FIELDS = (
    "customer_name_similarity",
    "email_similarity",
    "phone_similarity",
    "address_similarity",
    "city_similarity",
    "state_similarity",
    "postal_code_similarity",
    "country_similarity",
)

RUN_HISTORY_FIELDS = (
    "run_id",
    "timestamp_utc",
    "source_a_rows",
    "source_b_rows",
    "candidate_count",
    "eligible_count",
    "matched_count",
    "unmatched_count",
    "current_threshold",
    "match_coverage",
    "elapsed_seconds",
)
PATTERN_HISTORY_FIELDS = (
    "run_id",
    "timestamp_utc",
    "score_band",
    "candidate_pair_count",
    "eligible_pair_count",
    "average_score",
    "highest_score",
)
RULE_HISTORY_FIELDS = (
    "run_id",
    "timestamp_utc",
    "field",
    "configured_weight",
    "candidate_count",
    "available_count",
    "available_fraction",
    "average_similarity",
    "exact_similarity_count",
)
RECOMMENDATION_HISTORY_FIELDS = (
    "run_id",
    "timestamp_utc",
    "model_type",
    "current_threshold",
    "recommended_threshold",
    "historical_run_count",
    "historical_average_match_coverage",
    "rationale",
)
PERFORMANCE_HISTORY_FIELDS = (
    "run_id",
    "timestamp_utc",
    "operation",
    "duration_seconds",
    "process_rss_before_mib",
    "process_rss_after_mib",
    "process_rss_delta_mib",
    "process_rss_peak_sampled_mib",
    "process_peak_working_set_mib",
    "duckdb_memory_before_mib",
    "duckdb_memory_after_mib",
    "duckdb_temp_before_mib",
    "duckdb_temp_after_mib",
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        return list(csv.DictReader(input_file))


def _append_csv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, object]],
) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    if not needs_header:
        with path.open("r", encoding="utf-8-sig", newline="") as input_file:
            existing_fields = next(csv.reader(input_file), [])
        if existing_fields != list(fieldnames):
            raise ValueError(
                f"History schema mismatch in {path}: "
                f"expected {list(fieldnames)}, found {existing_fields}"
            )
    with path.open("a", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        if needs_header:
            writer.writeheader()
        writer.writerows(rows)


def _write_csv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: Iterable[dict[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _as_int(value: str | None) -> int:
    return int(value) if value else 0


def _legacy_row_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return len(_read_csv(path))


def bootstrap_history() -> None:
    """Import the previous one-run CSV reports before the matching run overwrites them."""
    if RUN_HISTORY.exists():
        return
    if not any(
        path.is_file()
        for path in (
            LEGACY_SCORE_SUMMARY,
            LEGACY_METRICS,
            LEGACY_MATCHED,
            LEGACY_UNMATCHED,
        )
    ):
        return

    timestamp = datetime.now(timezone.utc).isoformat()
    legacy_run_id = "legacy-csv-import"
    score_rows = _read_csv(LEGACY_SCORE_SUMMARY) if LEGACY_SCORE_SUMMARY.is_file() else []
    matched_count = _legacy_row_count(LEGACY_MATCHED)
    unmatched_rows = (
        _read_csv(LEGACY_UNMATCHED) if LEGACY_UNMATCHED.is_file() else []
    )
    source_a_unmatched = sum(row.get("source") == "source_a" for row in unmatched_rows)
    source_b_unmatched = sum(row.get("source") == "source_b" for row in unmatched_rows)
    source_a_count = matched_count + source_a_unmatched
    source_b_count = matched_count + source_b_unmatched
    candidate_count = sum(
        _as_int(row.get("candidate_pair_count")) for row in score_rows
    )
    eligible_count = sum(
        _as_int(row.get("eligible_pair_count")) for row in score_rows
    )
    coverage_denominator = min(source_a_count, source_b_count)
    legacy_metrics = _read_csv(LEGACY_METRICS) if LEGACY_METRICS.is_file() else []
    elapsed_seconds = round(
        sum(float(row.get("duration_seconds", 0) or 0) for row in legacy_metrics),
        6,
    )

    _append_csv(
        RUN_HISTORY,
        RUN_HISTORY_FIELDS,
        [
            {
                "run_id": legacy_run_id,
                "timestamp_utc": timestamp,
                "source_a_rows": source_a_count,
                "source_b_rows": source_b_count,
                "candidate_count": candidate_count,
                "eligible_count": eligible_count,
                "matched_count": matched_count,
                "unmatched_count": len(unmatched_rows),
                "current_threshold": "",
                "match_coverage": (
                    round(matched_count / coverage_denominator, 6)
                    if coverage_denominator
                    else ""
                ),
                "elapsed_seconds": elapsed_seconds,
            }
        ],
    )
    _append_csv(
        PATTERN_HISTORY,
        PATTERN_HISTORY_FIELDS,
        [
            {
                "run_id": legacy_run_id,
                "timestamp_utc": timestamp,
                **row,
            }
            for row in score_rows
        ],
    )
    _append_csv(
        PERFORMANCE_HISTORY,
        PERFORMANCE_HISTORY_FIELDS,
        [
            {
                "run_id": legacy_run_id,
                "timestamp_utc": timestamp,
                **row,
            }
            for row in legacy_metrics
        ],
    )


def _candidate_matrix(
    connection: duckdb.DuckDBPyConnection,
    fields: tuple[str, ...],
    source_customers: list[dict[str, str]],
    reference_customers: list[dict[str, str]],
) -> tuple[list[tuple[Any, ...]], list[list[float]]]:
    similarity_columns = ", ".join(
        f"candidate_scores.{field}_similarity" for field in fields
    )
    rows = connection.execute(
        f"""
        SELECT
            source_a.customer_id,
            source_b.customer_id,
            candidate_scores.source_index,
            candidate_scores.reference_index,
            candidate_scores.match_score,
            {similarity_columns}
        FROM candidate_scores
        JOIN source_a
            ON source_a.record_index = candidate_scores.source_index
        JOIN source_b ON source_b.record_index = candidate_scores.reference_index
        ORDER BY candidate_scores.source_index, candidate_scores.reference_index
        """
    ).fetchall()
    candidate_rows: list[tuple[Any, ...]] = []
    for row in rows:
        # Hash both complete records so review decisions survive reruns but
        # cannot accidentally attach to changed customer data.
        source_customer = source_customers[int(row[2])]
        reference_customer = reference_customers[int(row[3])]
        stable_record = json.dumps(
            [
                int(row[2]),
                source_customer,
                int(row[3]),
                reference_customer,
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        candidate_key = hashlib.sha256(stable_record.encode("utf-8")).hexdigest()
        candidate_rows.append((candidate_key, *row))

    features = [
        [
            float(value) if value is not None else 0.0
            for value in (*row[6:], row[5] / 100)
        ]
        for row in candidate_rows
    ]
    return candidate_rows, features


def _read_manual_reviews(review_file: Path) -> dict[str, dict[str, str]]:
    if not review_file.is_file():
        return {}
    logging.getLogger("duckdb_customer_matching").debug(
        "Reading existing reviewer decisions and comments from %s",
        review_file,
    )
    reviews: dict[str, dict[str, str]] = {}
    for row in _read_csv(review_file):
        candidate_key = row.get("candidate_key", "").strip()
        if not candidate_key:
            raise ValueError(
                f"Manual-review CSV row is missing candidate_key: {review_file}"
            )
        decision = row.get("review_decision", "").strip().lower()
        if decision not in ("", "approved", "rejected"):
            raise ValueError(
                f"Invalid review_decision {decision!r} for candidate "
                f"{candidate_key}; use approved, rejected, or blank."
            )
        if candidate_key in reviews:
            raise ValueError(
                f"Duplicate candidate_key {candidate_key!r} in {review_file}"
            )
        reviews[candidate_key] = {
            "review_decision": decision,
            "review_comment": row.get("review_comment", ""),
        }
    return reviews


def _read_steward_reviews(review_file: Path) -> dict[str, dict[str, str]]:
    if not review_file.is_file():
        return {}
    reviews: dict[str, dict[str, str]] = {}
    with review_file.open("r", encoding="utf-8") as input_file:
        document = yaml.safe_load(input_file)
    if not isinstance(document, dict) or not isinstance(
        document.get("candidates"), list
    ):
        raise ValueError(
            f"Steward YAML must contain a top-level 'candidates' list: {review_file}"
        )
    for index, row in enumerate(document["candidates"], start=1):
        if not isinstance(row, dict):
            raise ValueError(
                f"Candidate {index} in {review_file.name} must be a mapping."
            )
        candidate_key = str(row.get("candidate_key", "")).strip()
        if not candidate_key:
            raise ValueError(
                f"Steward-review CSV row is missing candidate_key: {review_file}"
            )
        decision = str(row.get("steward_decision") or "").strip().lower()
        if decision not in ("", "approve", "reject"):
            raise ValueError(
                f"Invalid steward_decision {decision!r} for candidate "
                f"{candidate_key}; use approve, reject, or blank."
            )
        if candidate_key in reviews:
            raise ValueError(
                f"Duplicate candidate_key {candidate_key!r} in {review_file}"
            )
        reviews[candidate_key] = {
            "review_decision": (
                "approved" if decision == "approve"
                else "rejected" if decision == "reject"
                else ""
            ),
            "review_comment": str(row.get("steward_comment") or ""),
        }
    return reviews


def validate_manual_review_file(review_file: Path) -> None:
    """Reject invalid reviewer edits before matching outputs are replaced."""
    _read_manual_reviews(review_file)


def _field_statistics(
    connection: duckdb.DuckDBPyConnection,
    run_id: str,
    timestamp: str,
    field_weights: dict[str, float],
    candidate_count: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for field, weight in field_weights.items():
        similarity = f"{field}_similarity"
        available_count, average_similarity, exact_count = connection.execute(
            f"""
            SELECT
                count({similarity}),
                avg({similarity}),
                count(*) FILTER (WHERE {similarity} = 1.0)
            FROM candidate_scores
            """
        ).fetchone()
        rows.append(
            {
                "run_id": run_id,
                "timestamp_utc": timestamp,
                "field": field,
                "configured_weight": weight,
                "candidate_count": candidate_count,
                "available_count": available_count,
                "available_fraction": (
                    round(available_count / candidate_count, 6)
                    if candidate_count
                    else 0
                ),
                "average_similarity": (
                    round(average_similarity, 6)
                    if average_similarity is not None
                    else ""
                ),
                "exact_similarity_count": exact_count,
            }
        )
    return rows


def _score_patterns(
    connection: duckdb.DuckDBPyConnection,
    run_id: str,
    timestamp: str,
    threshold: float,
) -> list[dict[str, object]]:
    results = connection.execute(
        """
        SELECT
            CASE
                WHEN match_score = 100 THEN '100 exact'
                WHEN match_score >= 80 THEN '80-99.99 fuzzy'
                WHEN match_score >= 40 THEN '40-79.99 review'
                ELSE '0-39.99 weak'
            END AS score_band,
            count(*) AS candidate_pair_count,
            count(*) FILTER (WHERE match_score >= ?) AS eligible_pair_count,
            round(avg(match_score), 2) AS average_score,
            round(max(match_score), 2) AS highest_score
        FROM candidate_scores
        GROUP BY score_band
        ORDER BY min(match_score) DESC
        """,
        [threshold],
    ).fetchall()
    return [
        {
            "run_id": run_id,
            "timestamp_utc": timestamp,
            "score_band": row[0],
            "candidate_pair_count": row[1],
            "eligible_pair_count": row[2],
            "average_score": row[3],
            "highest_score": row[4],
        }
        for row in results
    ]


def _train_similarity_clusters(
    rows: list[tuple[Any, ...]],
    features: list[list[float]],
    threshold: float,
    review_file: Path,
    selected_pairs: set[tuple[int, int]],
    auto_merge_threshold: float,
    steward_review_file: Path | None = None,
) -> tuple[float, str, int, int]:
    logger = logging.getLogger("duckdb_customer_matching")
    review_fields = (
        "candidate_key",
        "source_customer_id",
        "reference_customer_id",
        "source_index",
        "reference_index",
        "match_score",
        *SIMILARITY_FIELDS,
        "score_band",
        "model_cluster",
        "similarity_tier",
        "match_status",
        "auto_merge_candidate",
        "review_decision",
        "review_comment",
        "model_approval_score",
        "recommended_action",
    )
    previous_reviews = _read_manual_reviews(review_file)
    if steward_review_file is not None:
        steward_reviews = _read_steward_reviews(steward_review_file)
        previous_reviews.update(steward_reviews)
        current_candidate_keys = {str(row[0]) for row in rows}
        stale_review_count = len(set(steward_reviews) - current_candidate_keys)
        if stale_review_count:
            logger.warning(
                "Ignoring %d steward review(s) whose candidate keys are not "
                "present in the current input data",
                stale_review_count,
            )
    if not rows:
        _write_csv(review_file, review_fields, [])
        _write_csv(AUTO_MERGE_CANDIDATES, review_fields, [])
        return threshold, "not_fit_no_candidates", 0, 0

    cluster_by_candidate: dict[str, int] = {}
    cluster_tier: dict[int, str] = {}
    cluster_means: dict[int, float] = {}
    if len(rows) >= MINIMUM_CLUSTER_CANDIDATES:
        unique_features = len({tuple(row) for row in features})
        if unique_features > 1:
            # KMeans groups pairs by their field-similarity patterns. The
            # clusters describe patterns, not verified match identities.
            cluster_count = min(3, unique_features)
            assignments = KMeans(
                n_clusters=cluster_count, random_state=42, n_init=10
            ).fit_predict(features)
            observed_clusters = sorted({int(cluster) for cluster in assignments})
            cluster_means = {
                cluster: sum(
                    float(rows[index][5])
                    for index, assigned_cluster in enumerate(assignments)
                    if assigned_cluster == cluster
                )
                / sum(assigned_cluster == cluster for assigned_cluster in assignments)
                for cluster in observed_clusters
            }
            ordered_clusters = sorted(cluster_means, key=cluster_means.get)
            tiers = ("low_similarity", "ambiguous", "high_similarity")
            if len(ordered_clusters) == 1:
                tiers = ("single_cluster",)
            elif len(ordered_clusters) == 2:
                tiers = ("low_similarity", "high_similarity")
            cluster_tier = dict(zip(ordered_clusters, tiers))
            cluster_by_candidate = {
                str(row[0]): int(cluster)
                for row, cluster in zip(rows, assignments)
            }

    labeled_indices = [
        index
        for index, row in enumerate(rows)
        if previous_reviews.get(str(row[0]), {}).get("review_decision")
        in ("approved", "rejected")
    ]
    approved_count = sum(
        previous_reviews[str(rows[index][0])]["review_decision"] == "approved"
        for index in labeled_indices
    )
    rejected_count = len(labeled_indices) - approved_count
    logger.info(
        "Manual review labels available for this input: %d approved, %d rejected",
        approved_count,
        rejected_count,
    )
    supervised_model: LogisticRegression | None = None
    if (
        len(labeled_indices) >= MINIMUM_REVIEW_LABELS
        and approved_count >= MINIMUM_LABELS_PER_CLASS
        and rejected_count >= MINIMUM_LABELS_PER_CLASS
    ):
        # Reviewer decisions become labels only after both outcomes have
        # enough examples; otherwise the model would learn a one-sided rule.
        labels = [
            previous_reviews[str(rows[index][0])]["review_decision"] == "approved"
            for index in labeled_indices
        ]
        supervised_model = LogisticRegression(
            class_weight="balanced", max_iter=1000, random_state=42
        )
        supervised_model.fit(
            [features[index] for index in labeled_indices],
            labels,
        )
        logger.info(
            "Trained recommendation model using %d reviewed candidates; "
            "it ranks unreviewed candidates for people to inspect",
            len(labeled_indices),
        )
    elif labeled_indices:
        logger.info(
            "Not enough balanced manual-review examples to train the "
            "recommendation model; at least %d labels and %d per decision "
            "are required",
            MINIMUM_REVIEW_LABELS,
            MINIMUM_LABELS_PER_CLASS,
        )

    # Predict all unreviewed rows in one batch instead of repeatedly invoking
    # the model for each pair.
    approved_probabilities: dict[int, float] = {}
    if supervised_model is not None:
        unreviewed_indices = [
            index
            for index, row in enumerate(rows)
            if previous_reviews.get(str(row[0]), {}).get("review_decision")
            not in ("approved", "rejected")
        ]
        if unreviewed_indices:
            probabilities = supervised_model.predict_proba(
                [features[index] for index in unreviewed_indices]
            )
            approved_probabilities = {
                index: float(probability[1])
                for index, probability in zip(unreviewed_indices, probabilities)
            }

    recommendation_fields = (
        "candidate_key",
        "source_customer_id",
        "reference_customer_id",
        "source_index",
        "reference_index",
        "match_score",
        *SIMILARITY_FIELDS,
        "score_band",
        "model_cluster",
        "similarity_tier",
        "match_status",
        "auto_merge_candidate",
        "review_decision",
        "review_comment",
        "model_approval_score",
        "recommended_action",
    )
    auto_merge_rows: list[dict[str, object]] = []
    def build_review_rows() -> Iterator[dict[str, object]]:
        for index, row in enumerate(rows):
            candidate_key = str(row[0])
            score = float(row[5])
            cluster = cluster_by_candidate.get(candidate_key)
            tier = cluster_tier.get(cluster, "insufficient_history")
            review = previous_reviews.get(
                candidate_key, {"review_decision": "", "review_comment": ""}
            )
            decision = review["review_decision"]
            probability: float | str = ""
            if decision == "approved":
                action = "approved_by_reviewer"
            elif decision == "rejected":
                action = "rejected_by_reviewer"
            elif supervised_model is not None:
                probability_value = approved_probabilities[index]
                probability = round(probability_value, 4)
                if probability_value >= 0.8:
                    action = "recommend_approval_review"
                elif probability_value <= 0.2:
                    action = "recommend_rejection_review"
                else:
                    action = "manual_review"
            elif tier == "high_similarity" and score >= threshold:
                action = "prioritize_manual_review"
            elif tier == "low_similarity":
                action = "deprioritize"
            else:
                action = "manual_review"

            selected = (int(row[3]), int(row[4])) in selected_pairs
            # A high score is not itself a merge: only selected one-to-one
            # pairs enter this separate review queue, and rejected pairs stay out.
            auto_merge_candidate = (
                selected and score >= auto_merge_threshold and decision != "rejected"
            )
            band = (
                "100 exact" if score == 100 else
                "80-99.99 fuzzy" if score >= 80 else
                "40-79.99 review" if score >= 40 else
                "0-39.99 weak"
            )
            result: dict[str, object] = {
                "candidate_key": candidate_key,
                "source_customer_id": row[1],
                "reference_customer_id": row[2],
                "source_index": row[3],
                "reference_index": row[4],
                "match_score": round(score, 2),
                **{
                    field: (
                        round(float(value), 4) if value is not None else ""
                    )
                    for field, value in zip(SIMILARITY_FIELDS, row[6:])
                },
                "score_band": band,
                "model_cluster": cluster if cluster is not None else "",
                "similarity_tier": tier,
                "match_status": (
                    "selected_match" if selected else "alternative_candidate"
                ),
                "auto_merge_candidate": "yes" if auto_merge_candidate else "no",
                "review_decision": decision,
                "review_comment": review["review_comment"],
                "model_approval_score": probability,
                "recommended_action": action,
            }
            if auto_merge_candidate:
                auto_merge_rows.append(result)
            yield result

    _write_csv(review_file, recommendation_fields, build_review_rows())
    _write_csv(AUTO_MERGE_CANDIDATES, recommendation_fields, auto_merge_rows)
    logger.info(
        "Wrote %d candidate review rows and %d auto-merge candidates "
        "(threshold %.2f; no source records changed)",
        len(rows),
        len(auto_merge_rows),
        auto_merge_threshold,
    )

    if supervised_model is not None:
        recommended_threshold = threshold
        model_type = "supervised_logistic_regression"
    elif len(cluster_means) > 1:
        ordered_clusters = sorted(cluster_means, key=cluster_means.get)
        recommended_threshold = round(
            (cluster_means[ordered_clusters[-1]] + cluster_means[ordered_clusters[-2]])
            / 2,
            2,
        )
        model_type = f"unsupervised_kmeans_{len(cluster_means)}_clusters"
    else:
        recommended_threshold = threshold
        model_type = (
            "not_fit_insufficient_reviews"
            if len(labeled_indices) < MINIMUM_REVIEW_LABELS
            else "not_fit_insufficient_clusters"
        )
    return recommended_threshold, model_type, len(labeled_indices), len(auto_merge_rows)


def _historical_context() -> tuple[int, float | str]:
    if not RUN_HISTORY.is_file():
        return 0, ""
    history = _read_csv(RUN_HISTORY)
    coverage = [
        float(row["match_coverage"])
        for row in history
        if row.get("match_coverage")
    ]
    return (
        len(history),
        round(sum(coverage) / len(coverage), 6) if coverage else "",
    )


def run_analytics(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    threshold: float,
    source_a_count: int,
    source_b_count: int,
    candidate_count: int,
    eligible_count: int,
    matched_count: int,
    unmatched_count: int,
    elapsed_seconds: float,
    field_weights: dict[str, float],
    source_customers: list[dict[str, str]],
    reference_customers: list[dict[str, str]],
    selected_pairs: set[tuple[int, int]],
    review_file: Path,
    auto_merge_threshold: float,
    steward_review_file: Path | None = None,
) -> dict[str, object]:
    """Capture rule and score history, cluster candidates, and write recommendations."""
    timestamp = datetime.now(timezone.utc).isoformat()
    fields = tuple(field_weights)
    rule_rows = _field_statistics(
        connection, run_id, timestamp, field_weights, candidate_count
    )
    pattern_rows = _score_patterns(connection, run_id, timestamp, threshold)
    candidate_rows, features = _candidate_matrix(
        connection, fields, source_customers, reference_customers
    )
    (
        recommended_threshold,
        model_type,
        reviewed_count,
        auto_merge_candidate_count,
    ) = _train_similarity_clusters(
        candidate_rows,
        features,
        threshold,
        review_file,
        selected_pairs,
        auto_merge_threshold,
        steward_review_file,
    )
    historical_run_count, historical_average_coverage = _historical_context()
    match_coverage = (
        round(matched_count / min(source_a_count, source_b_count), 6)
        if min(source_a_count, source_b_count)
        else 0
    )
    rationale = _recommendation_rationale(
        model_type, threshold, recommended_threshold, reviewed_count
    )
    run_row = {
        "run_id": run_id,
        "timestamp_utc": timestamp,
        "source_a_rows": source_a_count,
        "source_b_rows": source_b_count,
        "candidate_count": candidate_count,
        "eligible_count": eligible_count,
        "matched_count": matched_count,
        "unmatched_count": unmatched_count,
        "current_threshold": threshold,
        "match_coverage": match_coverage,
        "elapsed_seconds": round(elapsed_seconds, 6),
    }
    recommendation_row = {
        "run_id": run_id,
        "timestamp_utc": timestamp,
        "model_type": model_type,
        "current_threshold": threshold,
        "recommended_threshold": recommended_threshold,
        "historical_run_count": historical_run_count,
        "historical_average_match_coverage": historical_average_coverage,
        "rationale": rationale,
    }

    _append_csv(RUN_HISTORY, RUN_HISTORY_FIELDS, [run_row])
    _append_csv(RULE_HISTORY, RULE_HISTORY_FIELDS, rule_rows)
    _append_csv(PATTERN_HISTORY, PATTERN_HISTORY_FIELDS, pattern_rows)
    _append_csv(
        RECOMMENDATION_HISTORY,
        RECOMMENDATION_HISTORY_FIELDS,
        [recommendation_row],
    )
    _write_csv(
        CURRENT_RECOMMENDATION,
        RECOMMENDATION_HISTORY_FIELDS,
        [recommendation_row],
    )
    return {
        "recommended_threshold": recommended_threshold,
        "model_type": model_type,
        "recommendation_file": str(CURRENT_RECOMMENDATION),
        "review_file": str(review_file),
        "auto_merge_candidate_count": auto_merge_candidate_count,
    }


def _recommendation_rationale(
    model_type: str,
    current_threshold: float,
    recommended_threshold: float,
    reviewed_count: int,
) -> str:
    if model_type == "supervised_logistic_regression":
        return (
            f"Trained on {reviewed_count} human-approved/rejected candidate labels. "
            "Probability scores prioritize human review only; the current "
            "production threshold is unchanged."
        )
    if model_type.startswith("unsupervised_kmeans_"):
        return (
            f"Threshold suggestion {recommended_threshold:.2f} comes from "
            "similarity-cluster score separation and is not validated accuracy. "
            "Human review is still required."
        )
    return (
        f"Not enough suitable reviewed labels or distinct score clusters to fit "
        f"a recommendation model. Keep the current threshold "
        f"({current_threshold:.2f}) and review candidates manually."
    )


def append_performance_history(
    run_id: str, operation_metrics: list[dict[str, object]]
) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    _append_csv(
        PERFORMANCE_HISTORY,
        PERFORMANCE_HISTORY_FIELDS,
        [
            {
                "run_id": run_id,
                "timestamp_utc": timestamp,
                **metric,
            }
            for metric in operation_metrics
        ],
    )
