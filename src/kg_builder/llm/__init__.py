"""Local-only LLM adapters for natural-language KG queries."""

from .client import LocalLLMSettings, OllamaClient
from .models import PlanningStatus

__all__ = ["LocalLLMSettings", "OllamaClient", "PlanningStatus"]
