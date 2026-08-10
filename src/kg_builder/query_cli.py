"""Command-line entry point for structured, read-only verified KG queries."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from .config import ConfigurationError, Neo4jSettings
from .query_contracts import QueryRequest, QueryValidationError
from .query_service import Neo4jReadExecutor, QueryService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request",
        required=True,
        help="structured request JSON; arbitrary Cypher is not accepted",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(args.request)
        request = QueryRequest.from_dict(payload)
        settings = Neo4jSettings.from_env()
        with GraphDatabase.driver(
            settings.uri, auth=(settings.user, settings.password)
        ) as driver:
            driver.verify_connectivity()
            service = QueryService(Neo4jReadExecutor(driver, settings.database))
            response = service.execute(request)
        print(json.dumps(response.to_dict(), ensure_ascii=False, indent=2))
        return 0
    except (json.JSONDecodeError, QueryValidationError, ConfigurationError) as exc:
        print(f"request error: {exc}", file=sys.stderr)
        return 2
    except Neo4jError as exc:
        print(f"Neo4j query failed: {exc.code or exc.__class__.__name__}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
