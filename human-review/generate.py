import argparse
import logging
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from customer_matching.steward_review import (  # noqa: E402
    STEWARD_REVIEW_YAML,
    generate_steward_review,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a compact, editable YAML file for customer-match stewards."
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate the steward YAML from analytics/candidate_review.csv.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum pending candidates to include (default: 100).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=STEWARD_REVIEW_YAML,
        help=f"Steward YAML output path (default: {STEWARD_REVIEW_YAML}).",
    )
    args = parser.parse_args()
    if not args.generate:
        parser.error("specify --generate")
    if args.limit < 1:
        parser.error("--limit must be at least 1")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    generate_steward_review(args.limit, args.output)


if __name__ == "__main__":
    main()
