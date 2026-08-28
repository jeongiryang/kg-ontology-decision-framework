"""Provider-neutral structured local LLM clients for query planning and Cypher."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import StrEnum
from time import perf_counter
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from dotenv import load_dotenv

from .models import LLMGeneration


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
MAX_RESPONSE_BYTES = 1_048_576
HTTP_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
# vLLM's OpenAI-compatible structured-output grammar does not implement this
# standard JSON Schema assertion.  Callers still validate uniqueness when they
# materialize their typed contracts; omitting it from the provider grammar avoids
# turning otherwise valid plans into HTTP 400 responses and deterministic fallback.
OPENAI_GRAMMAR_OMITTED_KEYWORDS = frozenset({"uniqueItems"})


class LLMConfigurationError(ValueError):
    pass


class LLMResponseError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class LLMProvider(StrEnum):
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai-compatible"


class _HTTPRedirectRejected(RuntimeError):
    """Internal signal that intentionally omits Location and response content."""


class _RejectAllRedirects(urllib.request.HTTPRedirectHandler):
    """Reject every redirect before urllib can construct a follow-up request."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        del req, fp, code, msg, headers, newurl
        raise _HTTPRedirectRejected


@dataclass(frozen=True, slots=True)
class LLMSettings:
    provider: LLMProvider
    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False, compare=False)
    timeout_seconds: float = 60.0
    max_retries: int = 1
    temperature: float = 0.0
    context_length: int = 8192
    max_output_tokens: int = 2048

    @classmethod
    def from_env(cls, *, dotenv_path: str | None = None) -> "LLMSettings":
        load_dotenv(dotenv_path=dotenv_path, override=False)
        provider_text = os.getenv("KG_LLM_PROVIDER", "").strip().lower()
        try:
            provider = LLMProvider(provider_text)
        except ValueError as exc:
            raise LLMConfigurationError(
                "KG_LLM_PROVIDER must be ollama or openai-compatible"
            ) from exc
        base_url = os.getenv("KG_LLM_BASE_URL", "").strip().rstrip("/")
        model = os.getenv("KG_LLM_MODEL", "").strip()
        if not base_url or not model:
            raise LLMConfigurationError("KG_LLM_BASE_URL and KG_LLM_MODEL are required")
        try:
            settings = cls(
                provider=provider,
                base_url=base_url,
                model=model,
                api_key=os.getenv("KG_LLM_API_KEY") or None,
                timeout_seconds=float(os.getenv("KG_LLM_TIMEOUT_SECONDS", "60")),
                max_retries=int(os.getenv("KG_LLM_MAX_RETRIES", "1")),
                temperature=float(os.getenv("KG_LLM_TEMPERATURE", "0")),
                context_length=int(os.getenv("KG_LLM_CONTEXT_LENGTH", "8192")),
                max_output_tokens=int(os.getenv("KG_LLM_MAX_OUTPUT_TOKENS", "2048")),
            )
        except ValueError as exc:
            raise LLMConfigurationError("local LLM numeric settings are invalid") from exc
        settings.validate()
        return settings

    def validate(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
            raise LLMConfigurationError("KG_LLM_BASE_URL must be loopback HTTP only")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise LLMConfigurationError("KG_LLM_BASE_URL must not contain credentials")
        normalized_path = parsed.path.rstrip("/")
        allowed_paths = (
            {""}
            if self.provider is LLMProvider.OLLAMA
            else {"", "/v1"}
        )
        if normalized_path not in allowed_paths:
            raise LLMConfigurationError(
                f"KG_LLM_BASE_URL path is invalid for provider {self.provider.value}"
            )
        if not self.model:
            raise LLMConfigurationError("KG_LLM_MODEL must be non-empty")
        if not 1 <= self.timeout_seconds <= 600:
            raise LLMConfigurationError("KG_LLM_TIMEOUT_SECONDS must be between 1 and 600")
        if self.max_retries not in {0, 1}:
            raise LLMConfigurationError("KG_LLM_MAX_RETRIES must be 0 or 1")
        if self.temperature != 0:
            raise LLMConfigurationError("PoC requires KG_LLM_TEMPERATURE=0")
        if not 2048 <= self.context_length <= 131_072:
            raise LLMConfigurationError("KG_LLM_CONTEXT_LENGTH must be 2048..131072")
        if not 1 <= self.max_output_tokens <= 16_384:
            raise LLMConfigurationError("KG_LLM_MAX_OUTPUT_TOKENS must be 1..16384")

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if self.provider is LLMProvider.OLLAMA:
            return base + "/api/chat"
        if urlparse(base).path.rstrip("/") == "/v1":
            return base + "/chat/completions"
        return base + "/v1/chat/completions"


# Kept as a source-compatible name for callers created in PR #15.  New code should
# use LLMSettings because the settings now describe more than one local provider.
LocalLLMSettings = LLMSettings


class StructuredLLMClient(Protocol):
    model: str

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, Any],
    ) -> LLMGeneration: ...


class _HTTPStructuredClient:
    def __init__(self, settings: LLMSettings):
        settings.validate()
        self.settings = settings
        self.model = settings.model
        self._opener = urllib.request.build_opener(_RejectAllRedirects())

    def _post(self, payload: Mapping[str, Any], headers: Mapping[str, str]) -> dict[str, Any]:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_transport_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            request = urllib.request.Request(
                self.settings.endpoint,
                data=encoded,
                headers=dict(headers),
                method="POST",
            )
            try:
                with self._opener.open(
                    request, timeout=self.settings.timeout_seconds
                ) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise LLMResponseError(
                        "LLM_RESPONSE_TOO_LARGE", "model response is too large"
                    )
                try:
                    envelope = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise LLMResponseError(
                        "LLM_INVALID_JSON", "provider response is not valid JSON"
                    ) from exc
                if not isinstance(envelope, dict):
                    raise LLMResponseError(
                        "LLM_RESPONSE_ENVELOPE_INVALID",
                        "provider response must be a JSON object",
                    )
                return envelope
            except LLMResponseError:
                raise
            except _HTTPRedirectRejected as exc:
                raise LLMResponseError(
                    "LLM_HTTP_REDIRECT_REJECTED",
                    (
                        f"{self.settings.provider.value} rejected an HTTP 3xx redirect; "
                        "the request was not retried"
                    ),
                ) from exc
            except urllib.error.HTTPError as exc:
                # Defense in depth if another urllib handler surfaces a redirect as
                # HTTPError instead of invoking _RejectAllRedirects.
                if exc.code in HTTP_REDIRECT_STATUS_CODES:
                    raise LLMResponseError(
                        "LLM_HTTP_REDIRECT_REJECTED",
                        (
                            f"{self.settings.provider.value} rejected an HTTP 3xx redirect; "
                            "the request was not retried"
                        ),
                    ) from exc
                last_transport_error = exc
                if attempt >= self.settings.max_retries:
                    raise LLMResponseError(
                        "LLM_HTTP_ERROR", f"local model HTTP request failed with status {exc.code}"
                    ) from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                last_transport_error = exc
                if attempt >= self.settings.max_retries:
                    break
        raise LLMResponseError(
            "LLM_UNAVAILABLE",
            f"local model request failed: {type(last_transport_error).__name__}",
        )

    @staticmethod
    def _decode_content(content: Any) -> dict[str, Any]:
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("LLM_RESPONSE_MISSING", "model returned no JSON content")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMResponseError("LLM_INVALID_JSON", "model content is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise LLMResponseError("LLM_JSON_OBJECT_REQUIRED", "model JSON must be an object")
        return payload


def _project_openai_grammar_schema(value: Any) -> Any:
    """Return the lossless-enough grammar subset accepted by local vLLM.

    This projection is used only to constrain provider generation.  It does not
    weaken the authoritative application contract: callers that use uniqueness
    assertions validate those collections while materializing their typed contracts.
    """

    if isinstance(value, Mapping):
        return {
            key: _project_openai_grammar_schema(item)
            for key, item in value.items()
            if key not in OPENAI_GRAMMAR_OMITTED_KEYWORDS
        }
    if isinstance(value, list):
        return [_project_openai_grammar_schema(item) for item in value]
    if isinstance(value, tuple):
        return [_project_openai_grammar_schema(item) for item in value]
    return value


class OllamaClient(_HTTPStructuredClient):
    """Ollama adapter; raw model content is parsed in memory and never traced."""

    def __init__(self, settings: LLMSettings):
        if settings.provider is not LLMProvider.OLLAMA:
            raise LLMConfigurationError("OllamaClient requires provider=ollama")
        super().__init__(settings)

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, Any],
    ) -> LLMGeneration:
        started = perf_counter()
        envelope = self._post(
            {
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
                    "num_predict": self.settings.max_output_tokens,
                },
            },
            {"Content-Type": "application/json"},
        )
        content = envelope.get("message", {}).get("content")
        return LLMGeneration(
            self._decode_content(content), perf_counter() - started, self.model
        )


class OpenAICompatibleClient(_HTTPStructuredClient):
    """Minimal loopback-only Chat Completions adapter suitable for local vLLM."""

    def __init__(self, settings: LLMSettings):
        if settings.provider is not LLMProvider.OPENAI_COMPATIBLE:
            raise LLMConfigurationError(
                "OpenAICompatibleClient requires provider=openai-compatible"
            )
        super().__init__(settings)

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, Any],
    ) -> LLMGeneration:
        started = perf_counter()
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        envelope = self._post(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": self.settings.temperature,
                "max_tokens": self.settings.max_output_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "kg_structured_response",
                        "strict": True,
                        "schema": _project_openai_grammar_schema(response_schema),
                    },
                },
            },
            headers,
        )
        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMResponseError("LLM_CHOICES_MISSING", "provider returned no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise LLMResponseError("LLM_CHOICE_INVALID", "provider choice must be an object")
        message = first.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        return LLMGeneration(
            self._decode_content(content), perf_counter() - started, self.model
        )


def create_llm_client(settings: LLMSettings) -> StructuredLLMClient:
    """The only provider dispatch point used by CLI and future web composition roots."""

    settings.validate()
    if settings.provider is LLMProvider.OLLAMA:
        return OllamaClient(settings)
    if settings.provider is LLMProvider.OPENAI_COMPATIBLE:
        return OpenAICompatibleClient(settings)
    raise LLMConfigurationError(f"unsupported LLM provider: {settings.provider}")
