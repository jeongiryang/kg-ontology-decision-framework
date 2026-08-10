"""Minimal local Ollama JSON client with no cloud or API-key fallback."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from dotenv import load_dotenv

from .models import LLMGeneration


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})


class LLMConfigurationError(ValueError):
    pass


class LLMResponseError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class LocalLLMSettings:
    base_url: str
    model: str
    timeout_seconds: float = 60.0
    max_retries: int = 1
    temperature: float = 0.0
    context_length: int = 8192

    @classmethod
    def from_env(cls, *, dotenv_path: str | None = None) -> "LocalLLMSettings":
        load_dotenv(dotenv_path=dotenv_path, override=False)
        base_url = os.getenv("KG_LLM_BASE_URL", "").strip().rstrip("/")
        model = os.getenv("KG_LLM_MODEL", "").strip()
        if not base_url or not model:
            raise LLMConfigurationError("KG_LLM_BASE_URL and KG_LLM_MODEL are required")
        try:
            settings = cls(
                base_url=base_url,
                model=model,
                timeout_seconds=float(os.getenv("KG_LLM_TIMEOUT_SECONDS", "60")),
                max_retries=int(os.getenv("KG_LLM_MAX_RETRIES", "1")),
                temperature=float(os.getenv("KG_LLM_TEMPERATURE", "0")),
                context_length=int(os.getenv("KG_LLM_CONTEXT_LENGTH", "8192")),
            )
        except ValueError as exc:
            raise LLMConfigurationError("local LLM numeric settings are invalid") from exc
        settings.validate()
        return settings

    def validate(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
            raise LLMConfigurationError("KG_LLM_BASE_URL must be local HTTP only")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise LLMConfigurationError("KG_LLM_BASE_URL must not contain credentials")
        if parsed.path not in {"", "/"}:
            raise LLMConfigurationError("KG_LLM_BASE_URL must not include an API path")
        if not 1 <= self.timeout_seconds <= 600:
            raise LLMConfigurationError("KG_LLM_TIMEOUT_SECONDS must be between 1 and 600")
        if self.max_retries not in {0, 1}:
            raise LLMConfigurationError("KG_LLM_MAX_RETRIES must be 0 or 1")
        if self.temperature != 0:
            raise LLMConfigurationError("PoC requires KG_LLM_TEMPERATURE=0")
        if not 2048 <= self.context_length <= 32768:
            raise LLMConfigurationError("KG_LLM_CONTEXT_LENGTH must be 2048..32768")


class StructuredLLMClient(Protocol):
    model: str

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, Any],
    ) -> LLMGeneration: ...


class OllamaClient:
    """Call Ollama's local chat endpoint and retain no raw model response."""

    MAX_RESPONSE_BYTES = 1_048_576

    def __init__(self, settings: LocalLLMSettings):
        self.settings = settings
        self.model = settings.model

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, Any],
    ) -> LLMGeneration:
        request_payload = {
            "model": self.model,
            "stream": False,
            "format": dict(response_schema),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {
                "temperature": self.settings.temperature,
                "num_ctx": self.settings.context_length,
            },
        }
        encoded = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        started = perf_counter()
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                request = urllib.request.Request(
                    self.settings.base_url + "/api/chat",
                    data=encoded,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(
                    request, timeout=self.settings.timeout_seconds
                ) as response:
                    raw = response.read(self.MAX_RESPONSE_BYTES + 1)
                if len(raw) > self.MAX_RESPONSE_BYTES:
                    raise LLMResponseError("LLM_RESPONSE_TOO_LARGE", "model response is too large")
                envelope = json.loads(raw)
                content = envelope.get("message", {}).get("content")
                if not isinstance(content, str):
                    raise LLMResponseError("LLM_RESPONSE_MISSING", "model returned no JSON content")
                payload = json.loads(content)
                if not isinstance(payload, dict):
                    raise LLMResponseError("LLM_JSON_OBJECT_REQUIRED", "model JSON must be an object")
                return LLMGeneration(payload, perf_counter() - started, self.model)
            except LLMResponseError:
                raise
            except (json.JSONDecodeError, urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                last_error = exc
                if attempt >= self.settings.max_retries:
                    break
        code = "LLM_INVALID_JSON" if isinstance(last_error, json.JSONDecodeError) else "LLM_UNAVAILABLE"
        raise LLMResponseError(code, f"local model request failed: {type(last_error).__name__}")
