"""Run one Korean natural-language question through the local safe query pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from neo4j import GraphDatabase

from kg_builder.config import ConfigurationError, Neo4jQuerySettings
from kg_builder.llm.client import LLMConfigurationError, LLMSettings, create_llm_client
from kg_builder.llm.cypher_generator import LocalCypherGenerator
from kg_builder.llm.planner import LocalQueryPlanner

from .natural_language_service import NaturalLanguageQueryService
from .query_executor import DynamicQueryExecutor
from .query_explainer import QueryExplainer
from .safety_pipeline import SafetyPipeline
from .schema_selector import QuerySchemaSelector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", help="Korean question; not persisted by default")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        llm_settings = LLMSettings.from_env()
        neo4j_settings = Neo4jQuerySettings.from_env()
        client = create_llm_client(llm_settings)
        with GraphDatabase.driver(
            neo4j_settings.uri,
            auth=(neo4j_settings.user, neo4j_settings.password),
        ) as driver:
            driver.verify_connectivity()
            pipeline = SafetyPipeline(
                QueryExplainer(driver, neo4j_settings.database),
                DynamicQueryExecutor(driver, neo4j_settings.database),
            )
            service = NaturalLanguageQueryService(
                LocalQueryPlanner(client),
                LocalCypherGenerator(client),
                pipeline,
                QuerySchemaSelector(),
                model=llm_settings.model,
                generator_retries=llm_settings.max_retries,
            )
            result = service.ask(args.question)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.status in {"ANSWERABLE", "NOT_FOUND"} else 2
    except (ConfigurationError, LLMConfigurationError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
