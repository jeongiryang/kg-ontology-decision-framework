"""Neo4j connection settings with local-only safety checks."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Raised when required or unsafe connection settings are supplied."""


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
LOCAL_BOLT_PORT = 7687
ALLOWED_SCHEMES = frozenset({"bolt", "neo4j"})


@dataclass(frozen=True, slots=True)
class Neo4jSettings:
    uri: str
    user: str
    password: str
    database: str = "neo4j"

    @classmethod
    def from_env(cls, *, dotenv_path: str | None = None) -> "Neo4jSettings":
        load_dotenv(dotenv_path=dotenv_path, override=False)
        values = {
            "NEO4J_URI": os.getenv("NEO4J_URI", "").strip(),
            "NEO4J_USER": os.getenv("NEO4J_USER", "").strip(),
            "NEO4J_PASSWORD": os.getenv("NEO4J_PASSWORD", ""),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ConfigurationError(
                "Missing required Neo4j environment variables: " + ", ".join(missing)
            )

        database = os.getenv("NEO4J_DATABASE", "neo4j").strip() or "neo4j"
        settings = cls(
            uri=values["NEO4J_URI"],
            user=values["NEO4J_USER"],
            password=values["NEO4J_PASSWORD"],
            database=database,
        )
        settings.validate_local_uri()
        return settings

    def validate_local_uri(self) -> None:
        parsed = urlparse(self.uri)
        if parsed.scheme not in ALLOWED_SCHEMES:
            raise ConfigurationError(
                "NEO4J_URI must use the local bolt:// or neo4j:// scheme"
            )
        if parsed.hostname not in LOCAL_HOSTS or parsed.port != LOCAL_BOLT_PORT:
            raise ConfigurationError(
                "NEO4J_URI must target localhost or 127.0.0.1 on port 7687"
            )
        if parsed.username or parsed.password or parsed.path not in {"", None}:
            raise ConfigurationError(
                "NEO4J_URI must not embed credentials or a path; use environment variables"
            )

    @property
    def endpoint(self) -> str:
        """Return a password-free host:port representation for logs."""
        parsed = urlparse(self.uri)
        return f"{parsed.hostname}:{parsed.port}"


@dataclass(frozen=True, slots=True)
class Neo4jQuerySettings:
    """Credentials for the isolated, read-only dynamic query account.

    These settings deliberately do not fall back to the ingestion credentials.  Neo4j
    authorization is the final security boundary for externally generated Cypher.
    """

    uri: str
    user: str
    password: str
    database: str = "neo4j"

    @classmethod
    def from_env(cls, *, dotenv_path: str | None = None) -> "Neo4jQuerySettings":
        load_dotenv(dotenv_path=dotenv_path, override=False)
        values = {
            "NEO4J_QUERY_URI": os.getenv("NEO4J_QUERY_URI", "").strip(),
            "NEO4J_QUERY_USER": os.getenv("NEO4J_QUERY_USER", "").strip(),
            "NEO4J_QUERY_PASSWORD": os.getenv("NEO4J_QUERY_PASSWORD", ""),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ConfigurationError(
                "Missing required read-only Neo4j query environment variables: "
                + ", ".join(missing)
            )
        database = os.getenv("NEO4J_QUERY_DATABASE", "neo4j").strip() or "neo4j"
        settings = cls(
            uri=values["NEO4J_QUERY_URI"],
            user=values["NEO4J_QUERY_USER"],
            password=values["NEO4J_QUERY_PASSWORD"],
            database=database,
        )
        settings.validate_local_uri()
        return settings

    def validate_local_uri(self) -> None:
        parsed = urlparse(self.uri)
        if parsed.scheme not in ALLOWED_SCHEMES:
            raise ConfigurationError(
                "NEO4J_QUERY_URI must use the local bolt:// or neo4j:// scheme"
            )
        if parsed.hostname not in LOCAL_HOSTS or parsed.port != LOCAL_BOLT_PORT:
            raise ConfigurationError(
                "NEO4J_QUERY_URI must target localhost or 127.0.0.1 on port 7687"
            )
        if parsed.username or parsed.password or parsed.path not in {"", None}:
            raise ConfigurationError(
                "NEO4J_QUERY_URI must not embed credentials or a path; use query environment variables"
            )

    @property
    def endpoint(self) -> str:
        parsed = urlparse(self.uri)
        return f"{parsed.hostname}:{parsed.port}"
