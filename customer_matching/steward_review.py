import csv
import hashlib
import logging
from collections import defaultdict
from pathlib import Path

import yaml

from analytics.analytics_pipeline import CANDIDATE_RECOMMENDATIONS
from customer_matching.main import (
    CUSTOMER_FIELDS,
    PROJECT_DIR,
    SOURCE_A_CSV,
    SOURCE_B_CSV,
)

HUMAN_REVIEW_DIR = PROJECT_DIR.parent / "human-review"
STEWARD_REVIEW_YAML = HUMAN_REVIEW_DIR / "steward_review.yaml"
SCORE_BAND_ORDER = (
    "100 exact",
    "80-99.99 fuzzy",
    "40-79.99 review",
    "0-39.99 weak",
)
MATCH_STATUS_ORDER = ("selected_match", "alternative_candidate")
LOGGER = logging.getLogger("duckdb_customer_matching")


def _read_steward_yaml(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as input_file:
        document = yaml.safe_load(input_file)
    if not isinstance(document, dict) or not isinstance(
        document.get("candidates"), list
    ):
        raise ValueError(
            f"Steward YAML must contain a top-level 'candidates' list: {path}"
        )
    candidates: list[dict[str, object]] = []
    for index, candidate in enumerate(document["candidates"], start=1):
        if not isinstance(candidate, dict):
            raise ValueError(
                f"Candidate {index} in {path.name} must be a mapping."
            )
        candidates.append(candidate)
    return candidates


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Required review file not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        return list(csv.DictReader(input_file))


def _customer_index(path: Path) -> dict[str, dict[str, str]]:
    customers = _read_rows(path)
    indexed = {row["customer_id"]: row for row in customers}
    if len(indexed) != len(customers):
        raise ValueError(f"Customer IDs must be unique in {path}")
    missing_fields = set(CUSTOMER_FIELDS) - set(customers[0]) if customers else set()
    if missing_fields:
        raise ValueError(
            f"{path.name} is missing required customer columns: "
            f"{', '.join(sorted(missing_fields))}"
        )
    return indexed


def _existing_decisions(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    decisions: dict[str, dict[str, str]] = {}
    for row in _read_steward_yaml(path):
        candidate_key = str(row.get("candidate_key", "")).strip()
        if not candidate_key:
            raise ValueError(f"Missing candidate_key in steward review file: {path}")
        if candidate_key in decisions:
            raise ValueError(
                f"Duplicate candidate_key {candidate_key!r} in steward review file"
            )
        decision = str(row.get("steward_decision") or "").strip().lower()
        if decision not in ("", "approve", "reject"):
            raise ValueError(
                f"Invalid steward_decision {decision!r} for {candidate_key}; "
                "use approve, reject, or blank."
            )
        decisions[candidate_key] = {
            "steward_decision": decision,
            "steward_comment": str(row.get("steward_comment") or ""),
        }
    return decisions


def _choose_candidates(
    rows: list[dict[str, object]],
    decisions: dict[str, dict[str, str]],
    limit: int,
) -> list[dict[str, object]]:
    reviewed_keys = {
        key
        for key, review in decisions.items()
        if review["steward_decision"] or review["steward_comment"]
    }
    by_stratum: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        if row["candidate_key"] not in reviewed_keys:
            details = row["match_details"]
            by_stratum[
                (str(details["score_band"]), str(details["match_status"]))
            ].append(row)

    for stratum_rows in by_stratum.values():
        stratum_rows.sort(
            key=lambda row: hashlib.sha256(
                row["candidate_key"].encode("utf-8")
            ).hexdigest()
        )

    selected = [
        row for row in rows
        if row["candidate_key"] in reviewed_keys
    ]
    remaining = max(0, limit - len(selected))
    strata = [
        (band, status)
        for band in SCORE_BAND_ORDER
        for status in MATCH_STATUS_ORDER
    ]
    positions = {stratum: 0 for stratum in strata}
    while remaining:
        added_this_round = False
        for stratum in strata:
            candidates = by_stratum.get(stratum, [])
            position = positions[stratum]
            if position < len(candidates):
                selected.append(candidates[position])
                positions[stratum] += 1
                remaining -= 1
                added_this_round = True
                if remaining == 0:
                    break
        if not added_this_round:
            break
    return selected


def generate_steward_review(
    limit: int = 100,
    output_path: Path = STEWARD_REVIEW_YAML,
) -> int:
    if limit < 1:
        raise ValueError("The steward review limit must be at least 1.")

    candidate_rows = _read_rows(CANDIDATE_RECOMMENDATIONS)
    if not candidate_rows:
        raise ValueError(
            f"No candidate rows found in {CANDIDATE_RECOMMENDATIONS}; "
            "run customer matching and analysis first."
        )
    required_columns = {
        "candidate_key",
        "source_customer_id",
        "reference_customer_id",
        "match_score",
        "score_band",
        "similarity_tier",
        "match_status",
        "auto_merge_candidate",
        "recommended_action",
        "customer_name_similarity",
        "email_similarity",
        "phone_similarity",
        "address_similarity",
        "city_similarity",
        "state_similarity",
        "postal_code_similarity",
        "country_similarity",
    }
    missing_columns = required_columns - set(candidate_rows[0])
    if missing_columns:
        raise ValueError(
            f"{CANDIDATE_RECOMMENDATIONS.name} is missing required columns: "
            f"{', '.join(sorted(missing_columns))}"
        )

    source_customers = _customer_index(SOURCE_A_CSV)
    reference_customers = _customer_index(SOURCE_B_CSV)
    decisions = _existing_decisions(output_path)
    candidate_keys = [row["candidate_key"] for row in candidate_rows]
    if len(candidate_keys) != len(set(candidate_keys)):
        raise ValueError(
            f"Duplicate candidate_key values in {CANDIDATE_RECOMMENDATIONS}"
        )

    current_keys = set(candidate_keys)
    stale_keys = set(decisions) - current_keys
    if stale_keys:
        LOGGER.warning(
            "Ignoring %d saved steward review(s) for candidates absent from the "
            "latest analysis",
            len(stale_keys),
        )

    enriched_rows: list[dict[str, object]] = []
    for candidate in candidate_rows:
        try:
            source = source_customers[candidate["source_customer_id"]]
            reference = reference_customers[candidate["reference_customer_id"]]
        except KeyError as error:
            raise ValueError(
                "Candidate review refers to an unknown customer ID: "
                f"{error.args[0]}"
            ) from error

        match_status = candidate["match_status"]
        score = float(candidate["match_score"])
        summary = (
            f"{match_status.replace('_', ' ')}; {candidate['score_band']}; "
            f"score {score:.2f}; {candidate['recommended_action']}"
        )
        enriched_rows.append({
            "candidate_key": candidate["candidate_key"],
            "candidate_summary": summary,
            "match_details": {
                field: candidate[field]
                for field in (
                    "match_score",
                    "score_band",
                    "similarity_tier",
                    "match_status",
                    "auto_merge_candidate",
                    "recommended_action",
                    *(f"{field}_similarity" for field in (
                        "customer_name",
                        "email",
                        "phone",
                        "address",
                        "city",
                        "state",
                        "postal_code",
                        "country",
                    )),
                )
            },
            "source_customer": source,
            "reference_customer": reference,
            **decisions.get(
                candidate["candidate_key"],
                {"steward_decision": "", "steward_comment": ""},
            ),
        })

    selected_rows = _choose_candidates(enriched_rows, decisions, limit)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "instructions": (
            "Edit only steward_decision (approve, reject, or blank) and "
            "steward_comment. Do not change candidate_key or customer/match details."
        ),
        "candidates": selected_rows,
    }
    with output_path.open("w", encoding="utf-8", newline="") as output_file:
        yaml.safe_dump(
            document,
            output_file,
            allow_unicode=True,
            sort_keys=False,
            width=100,
        )

    LOGGER.info(
        "Generated steward review YAML with %d candidate rows: %s",
        len(selected_rows),
        output_path,
    )
    return len(selected_rows)
