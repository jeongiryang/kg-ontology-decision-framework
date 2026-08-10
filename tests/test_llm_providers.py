from __future__ import annotations

import json
import os
import socket
import unittest
import urllib.error
from unittest.mock import patch

from kg_builder.llm.client import (
    LLMConfigurationError,
    LLMProvider,
    LLMResponseError,
    LLMSettings,
    OllamaClient,
    OpenAICompatibleClient,
    create_llm_client,
)


SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}


class FakeResponse:
    def __init__(self, payload: object, *, raw: bytes | None = None):
        self.raw = raw if raw is not None else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _size: int) -> bytes:
        return self.raw


def settings(
    provider: LLMProvider,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    max_retries: int = 1,
) -> LLMSettings:
    return LLMSettings(
        provider=provider,
        base_url=base_url
        or (
            "http://127.0.0.1:11434"
            if provider is LLMProvider.OLLAMA
            else "http://127.0.0.1:8000/v1"
        ),
        model="local-test-model",
        api_key=api_key,
        timeout_seconds=10,
        max_retries=max_retries,
        temperature=0,
        context_length=8192,
        max_output_tokens=512,
    )


class LLMProviderSettingsTests(unittest.TestCase):
    def test_factory_dispatch_and_protocol_contract(self) -> None:
        self.assertIsInstance(
            create_llm_client(settings(LLMProvider.OLLAMA)), OllamaClient
        )
        self.assertIsInstance(
            create_llm_client(settings(LLMProvider.OPENAI_COMPATIBLE)),
            OpenAICompatibleClient,
        )

    def test_environment_rejects_unknown_provider_and_cloud_endpoint(self) -> None:
        base = {
            "KG_LLM_BASE_URL": "http://127.0.0.1:11434",
            "KG_LLM_MODEL": "local-model",
        }
        with patch.dict(
            os.environ, {**base, "KG_LLM_PROVIDER": "unknown"}, clear=True
        ), self.assertRaises(LLMConfigurationError):
            LLMSettings.from_env(dotenv_path="/dev/null")
        with patch.dict(
            os.environ,
            {
                **base,
                "KG_LLM_PROVIDER": "ollama",
                "KG_LLM_BASE_URL": "http://models.example/v1",
            },
            clear=True,
        ), self.assertRaises(LLMConfigurationError):
            LLMSettings.from_env(dotenv_path="/dev/null")

    def test_provider_paths_and_v1_deduplication(self) -> None:
        ollama = settings(LLMProvider.OLLAMA)
        openai_v1 = settings(LLMProvider.OPENAI_COMPATIBLE)
        openai_root = settings(
            LLMProvider.OPENAI_COMPATIBLE,
            base_url="http://localhost:8000",
        )
        self.assertEqual(ollama.endpoint, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(
            openai_v1.endpoint, "http://127.0.0.1:8000/v1/chat/completions"
        )
        self.assertEqual(
            openai_root.endpoint, "http://localhost:8000/v1/chat/completions"
        )
        with self.assertRaises(LLMConfigurationError):
            settings(
                LLMProvider.OPENAI_COMPATIBLE,
                base_url="http://127.0.0.1:8000/v1/v1",
            ).validate()


class ProviderAdapterTests(unittest.TestCase):
    def test_ollama_request_and_response_contract(self) -> None:
        captured = {}

        def fake_open(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse({"message": {"content": '{"value": 7}'}})

        client = OllamaClient(settings(LLMProvider.OLLAMA))
        with patch("urllib.request.urlopen", side_effect=fake_open):
            result = client.generate_json(
                system_prompt="system",
                user_prompt="user",
                response_schema=SCHEMA,
            )
        request = captured["request"]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(body["format"], SCHEMA)
        self.assertEqual(body["options"]["num_ctx"], 8192)
        self.assertEqual(body["options"]["num_predict"], 512)
        self.assertEqual(result.payload, {"value": 7})

    def test_openai_request_optional_token_and_response_contract(self) -> None:
        requests = []

        def fake_open(request, timeout):
            requests.append(request)
            return FakeResponse(
                {"choices": [{"message": {"content": '{"value": 7}'}}]}
            )

        with patch("urllib.request.urlopen", side_effect=fake_open):
            result = OpenAICompatibleClient(
                settings(LLMProvider.OPENAI_COMPATIBLE, api_key="local-secret")
            ).generate_json(
                system_prompt="system",
                user_prompt="user",
                response_schema=SCHEMA,
            )
            OpenAICompatibleClient(
                settings(LLMProvider.OPENAI_COMPATIBLE)
            ).generate_json(
                system_prompt="system",
                user_prompt="user",
                response_schema=SCHEMA,
            )
        protected, unprotected = requests
        body = json.loads(protected.data)
        self.assertEqual(
            protected.full_url,
            "http://127.0.0.1:8000/v1/chat/completions",
        )
        self.assertEqual(protected.get_header("Authorization"), "Bearer local-secret")
        self.assertIsNone(unprotected.get_header("Authorization"))
        self.assertEqual(
            body["response_format"]["json_schema"]["schema"], SCHEMA
        )
        self.assertEqual(body["max_tokens"], 512)
        self.assertEqual(result.payload, {"value": 7})

    def test_both_adapters_return_the_same_generation_contract(self) -> None:
        responses = [
            FakeResponse({"message": {"content": '{"value": 7}'}}),
            FakeResponse({"choices": [{"message": {"content": '{"value": 7}'}}]}),
        ]
        with patch("urllib.request.urlopen", side_effect=responses):
            ollama = OllamaClient(settings(LLMProvider.OLLAMA)).generate_json(
                system_prompt="system", user_prompt="user", response_schema=SCHEMA
            )
            openai = OpenAICompatibleClient(
                settings(LLMProvider.OPENAI_COMPATIBLE)
            ).generate_json(
                system_prompt="system", user_prompt="user", response_schema=SCHEMA
            )
        self.assertEqual(ollama.payload, openai.payload)
        self.assertEqual(ollama.model, openai.model)

    def test_malformed_content_empty_choices_and_size_are_rejected(self) -> None:
        client = OllamaClient(settings(LLMProvider.OLLAMA, max_retries=0))
        with patch(
            "urllib.request.urlopen",
            return_value=FakeResponse({"message": {"content": "not-json"}}),
        ), self.assertRaises(LLMResponseError) as raised:
            client.generate_json(
                system_prompt="system", user_prompt="user", response_schema=SCHEMA
            )
        self.assertEqual(raised.exception.code, "LLM_INVALID_JSON")

        openai = OpenAICompatibleClient(
            settings(LLMProvider.OPENAI_COMPATIBLE, max_retries=0)
        )
        with patch(
            "urllib.request.urlopen", return_value=FakeResponse({"choices": []})
        ), self.assertRaises(LLMResponseError) as raised:
            openai.generate_json(
                system_prompt="system", user_prompt="user", response_schema=SCHEMA
            )
        self.assertEqual(raised.exception.code, "LLM_CHOICES_MISSING")
        with patch(
            "urllib.request.urlopen",
            return_value=FakeResponse(
                {"choices": [{"message": {"content": "not-json"}}]}
            ),
        ), self.assertRaises(LLMResponseError) as raised:
            openai.generate_json(
                system_prompt="system", user_prompt="user", response_schema=SCHEMA
            )
        self.assertEqual(raised.exception.code, "LLM_INVALID_JSON")

        for oversized_client in (client, openai):
            with self.subTest(client=type(oversized_client).__name__), patch(
                "urllib.request.urlopen",
                return_value=FakeResponse({}, raw=b"x" * (1_048_576 + 1)),
            ), self.assertRaises(LLMResponseError) as raised:
                oversized_client.generate_json(
                    system_prompt="system", user_prompt="user", response_schema=SCHEMA
                )
            self.assertEqual(raised.exception.code, "LLM_RESPONSE_TOO_LARGE")

    def test_timeout_and_http_errors_are_sanitized(self) -> None:
        clients = (
            OllamaClient(settings(LLMProvider.OLLAMA, max_retries=1)),
            OpenAICompatibleClient(
                settings(
                    LLMProvider.OPENAI_COMPATIBLE,
                    api_key="never-log-this-key",
                    max_retries=1,
                )
            ),
        )
        for client in clients:
            with self.subTest(client=type(client).__name__), patch(
                "urllib.request.urlopen", side_effect=socket.timeout("prompt payload")
            ) as request, self.assertRaises(LLMResponseError) as raised:
                client.generate_json(
                    system_prompt="private system",
                    user_prompt="private user",
                    response_schema=SCHEMA,
                )
            self.assertEqual(request.call_count, 2)
            self.assertEqual(raised.exception.code, "LLM_UNAVAILABLE")
            self.assertNotIn("never-log-this-key", str(raised.exception))
            self.assertNotIn("private user", str(raised.exception))

            error = urllib.error.HTTPError(
                client.settings.endpoint,
                401,
                "body contains never-log-this-key",
                {},
                None,
            )
            with patch(
                "urllib.request.urlopen", side_effect=error
            ), self.assertRaises(LLMResponseError) as raised:
                client.generate_json(
                    system_prompt="private system",
                    user_prompt="private user",
                    response_schema=SCHEMA,
                )
            self.assertEqual(raised.exception.code, "LLM_HTTP_ERROR")
            self.assertNotIn("never-log-this-key", str(raised.exception))
            self.assertNotIn("private user", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
