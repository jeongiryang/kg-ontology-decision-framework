"""Official entry point for future LLM-generated Cypher execution.

Concrete validators, approval objects, explainers, and executors are internal modules.
Python module privacy is not a security boundary; the Neo4j query account must also be
read-only.
"""

from .safety_pipeline import SafetyPipeline, SafetyPipelineError

__all__ = ["SafetyPipeline", "SafetyPipelineError"]
