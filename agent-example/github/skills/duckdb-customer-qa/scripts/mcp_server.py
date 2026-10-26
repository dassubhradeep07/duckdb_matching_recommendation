import logging
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from query_data import (
    open_connection,
    query_result,
    schema_result,
)

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
LOGGER = logging.getLogger("customer_data_mcp")
CONNECTION, AVAILABLE_TABLES = open_connection()
SERVER = FastMCP("customer-data")


@SERVER.tool()
def list_datasets() -> dict[str, Any]:
    """List local customer datasets and their available columns."""
    return schema_result(CONNECTION, AVAILABLE_TABLES)


@SERVER.tool()
def query_sql(sql: str, limit: int = 100) -> dict[str, Any]:
    """Run one read-only DuckDB SELECT on the registered customer datasets.

    Args:
        sql: A single SELECT statement that only refers to listed datasets.
        limit: Maximum rows returned, from 1 to 500.
    """
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    return query_result(CONNECTION, sql, limit)


def main() -> None:
    LOGGER.info("Starting customer-data MCP server with %d datasets", len(AVAILABLE_TABLES))
    try:
        SERVER.run(transport="stdio")
    finally:
        CONNECTION.close()


if __name__ == "__main__":
    main()
