import argparse
import logging
from pathlib import Path

import yaml

DEFAULT_REVIEW_YAML = Path(__file__).resolve().parent / "steward_review.yaml"
STANDARD_NOTES = {
    "approve": "Standard note: steward approved this candidate after review.",
    "reject": "Standard note: steward rejected this candidate after review.",
}
def fill_standard_notes(review_file: Path = DEFAULT_REVIEW_YAML) -> int:
    if not review_file.is_file():
        raise FileNotFoundError(f"Steward review YAML not found: {review_file}")

    with review_file.open("r", encoding="utf-8") as input_file:
        document = yaml.safe_load(input_file)
    if not isinstance(document, dict) or not isinstance(
        document.get("candidates"), list
    ):
        raise ValueError(
            f"Steward YAML must contain a top-level 'candidates' list: {review_file}"
        )

    updated_count = 0
    for index, row in enumerate(document["candidates"], start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Candidate {index} must be a mapping in {review_file}.")
        decision = str(row.get("steward_decision") or "").strip().lower()
        if decision not in ("", "approve", "reject"):
            raise ValueError(
                f"Invalid steward_decision {decision!r} on candidate {index}; "
                "use approve, reject, or blank."
            )
        comment = str(row.get("steward_comment") or "")
        if decision and not comment.strip():
            row["steward_comment"] = STANDARD_NOTES[decision]
            updated_count += 1

    if updated_count:
        with review_file.open("w", encoding="utf-8") as output_file:
            yaml.safe_dump(
                document,
                output_file,
                allow_unicode=True,
                sort_keys=False,
                width=100,
            )

    logging.getLogger(__name__).info(
        "Filled %d standard comment(s) in %s; steward decisions were not changed.",
        updated_count,
        review_file,
    )
    return updated_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fill blank comments for existing steward YAML approvals/rejections. "
            "Does not make steward decisions."
        )
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_REVIEW_YAML,
        help=f"Steward YAML file to update (default: {DEFAULT_REVIEW_YAML}).",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    fill_standard_notes(args.file)


if __name__ == "__main__":
    main()
