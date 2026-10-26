import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import duckdb

PROJECT_DIR = Path(__file__).resolve().parents[5]
DATASETS = {
    "source_a": PROJECT_DIR / "customer_matching" / "data" / "source_a.csv",
    "source_b": PROJECT_DIR / "customer_matching" / "data" / "source_b.csv",
    "matched_customers": (
        PROJECT_DIR / "customer_matching" / "matched_customers.csv"
    ),
    "unmatched_customers": (
        PROJECT_DIR / "customer_matching" / "unmatched_customers.csv"
    ),
    "candidate_scores": (
        PROJECT_DIR / "customer_matching" / "candidate_scores.csv"
    ),
    "candidate_score_summary": (
        PROJECT_DIR / "customer_matching" / "candidate_score_summary.csv"
    ),
    "auto_merge_candidates": (
        PROJECT_DIR / "customer_matching" / "auto_merge_candidates.csv"
    ),
    "candidate_review": PROJECT_DIR / "analytics" / "candidate_review.csv",
    "recommendation": PROJECT_DIR / "analytics" / "recommendation.csv",
}
FORBIDDEN_FUNCTIONS = re.compile(
    r"\b(?:read_csv|read_csv_auto|csv_scan|read_parquet|parquet_scan|"
    r"read_json|read_json_auto|read_text|read_blob|read_file|glob|"
    r"query|query_table|sqlite_scan|postgres_scan|mysql_scan)\s*\(",
    re.IGNORECASE,
)


def open_connection() -> tuple[duckdb.DuckDBPyConnection, list[str]]:
    source_paths = ("source_a", "source_b")
    missing_sources = [
        name for name in source_paths if not DATASETS[name].is_file()
    ]
    if missing_sources:
        paths = ", ".join(str(DATASETS[name]) for name in missing_sources)
        raise FileNotFoundError(f"Required customer input CSV not found: {paths}")

    connection = duckdb.connect(":memory:")
    available_tables: list[str] = []
    try:
        for table_name, path in DATASETS.items():
            if not path.is_file():
                continue
            connection.execute(
                f"""
                CREATE TABLE "{table_name}" AS
                SELECT * FROM read_csv(?, header=true, all_varchar=true)
                """,
                [str(path)],
            )
            available_tables.append(table_name)

        connection.execute("SET enable_external_access = false")
    except Exception:
        connection.close()
        raise
    return connection, available_tables


def validate_query(
    connection: duckdb.DuckDBPyConnection, sql: str
) -> None:
    if FORBIDDEN_FUNCTIONS.search(sql):
        raise ValueError(
            "File, external-source, and dynamic-query functions are not allowed."
        )
    statements = connection.extract_statements(sql)
    if len(statements) != 1:
        raise ValueError("Provide exactly one SQL statement.")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError("Only a read-only SELECT query is allowed.")


def schema_result(
    connection: duckdb.DuckDBPyConnection,
    available_tables: list[str],
) -> dict[str, Any]:
    tables: dict[str, list[dict[str, str]]] = {}
    for table_name in available_tables:
        columns = connection.execute(
            f'DESCRIBE SELECT * FROM "{table_name}"'
        ).fetchall()
        tables[table_name] = [
            {"name": str(column[0]), "type": str(column[1])}
            for column in columns
        ]
    return {"available_tables": tables}


def query_result(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    limit: int,
) -> dict[str, Any]:
    validate_query(connection, sql)
    cursor = connection.execute(sql)
    rows = cursor.fetchmany(limit + 1)
    truncated = len(rows) > limit
    rows = rows[:limit]
    column_names = [str(column[0]) for column in cursor.description or ()]
    result_rows = [
        {
            column_names[index]: _json_value(value)
            for index, value in enumerate(row)
        }
        for row in rows
    ]
    return {
        "query": sql,
        "columns": column_names,
        "returned_rows": len(result_rows),
        "truncated": truncated,
        "rows": result_rows,
    }


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a read-only DuckDB query over customer-matching CSVs."
    )
    query_group = parser.add_mutually_exclusive_group(required=True)
    query_group.add_argument(
        "--schema",
        action="store_true",
        help="List available dataset tables and columns.",
    )
    query_group.add_argument(
        "--sql",
        help="Run one read-only SELECT statement.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum result rows to return (1-500; default: 100).",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        parser.error("--limit must be between 1 and 500")

    connection: duckdb.DuckDBPyConnection | None = None
    try:
        connection, available_tables = open_connection()
        if args.schema:
            result = schema_result(connection, available_tables)
        else:
            result = query_result(connection, args.sql, args.limit)
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    except (duckdb.Error, OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}, indent=2), file=sys.stderr)
        return 2
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
