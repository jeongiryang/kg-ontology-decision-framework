from __future__ import annotations

import json
import os
import socket
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import kg_builder.llm.client as client_module
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


class MockRedirectTransport:
    """Exercise the production redirect handler without contacting a destination."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        self.initial_requests = []
        self.destination_requests = []
        self.destination_authorizations = []
        self.destination_bodies = []

    def open(self, request, timeout):
        del timeout
        self.initial_requests.append(request)
        handler = client_module._RejectAllRedirects()
        redirected_request = handler.redirect_request(
            request,
            None,
            self.status_code,
            "synthetic redirect",
            {"Location": "http://outside.invalid/secret-target"},
            "http://outside.invalid/secret-target",
        )
        if redirected_request is not None:  # pragma: no cover - security assertion
            self.destination_requests.append(redirected_request)
            self.destination_authorizations.append(
                redirected_request.get_header("Authorization")
            )
            self.destination_bodies.append(redirected_request.data)
        raise AssertionError("redirect handler unexpectedly returned")


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
        with patch.object(client._opener, "open", side_effect=fake_open):
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

    def test_ollama_grammar_projection_omits_only_unsupported_string_lengths(self) -> None:
        requests = []

        def fake_open(request, timeout):
            del timeout
            requests.append(request)
            return FakeResponse({"message": {"content": '{"value": "safe"}'}})

        client = OllamaClient(settings(LLMProvider.OLLAMA))
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 80,
                    "pattern": "^[a-z]+$",
                },
                "items": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 20},
                    "maxItems": 3,
                    "uniqueItems": True,
                },
            },
            "required": ["value"],
            "additionalProperties": False,
        }
        with patch.object(client._opener, "open", side_effect=fake_open):
            result = client.generate_json(
                system_prompt="system",
                user_prompt="user",
                response_schema=schema,
            )

        body = json.loads(requests[0].data)
        projected = body["format"]
        self.assertNotIn("minLength", projected["properties"]["value"])
        self.assertNotIn("maxLength", projected["properties"]["value"])
        self.assertNotIn("maxLength", projected["properties"]["items"]["items"])
        self.assertEqual(projected["properties"]["value"]["pattern"], "^[a-z]+$")
        self.assertEqual(projected["properties"]["items"]["maxItems"], 3)
        self.assertTrue(projected["properties"]["items"]["uniqueItems"])
        self.assertEqual(result.payload, {"value": "safe"})

    def test_openai_request_optional_token_and_response_contract(self) -> None:
        requests = []

        def fake_open(request, timeout):
            requests.append(request)
            return FakeResponse(
                {"choices": [{"message": {"content": '{"value": 7}'}}]}
            )

        protected_client = OpenAICompatibleClient(
            settings(LLMProvider.OPENAI_COMPATIBLE, api_key="local-secret")
        )
        unprotected_client = OpenAICompatibleClient(
            settings(LLMProvider.OPENAI_COMPATIBLE)
        )
        with patch.object(
            protected_client._opener, "open", side_effect=fake_open
        ), patch.object(unprotected_client._opener, "open", side_effect=fake_open):
            result = protected_client.generate_json(
                system_prompt="system",
                user_prompt="user",
                response_schema=SCHEMA,
            )
            unprotected_client.generate_json(
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

    def test_openai_grammar_projection_omits_only_provider_unsupported_uniqueness(self) -> None:
        requests = []

        def fake_open(request, timeout):
            del timeout
            requests.append(request)
            return FakeResponse(
                {"choices": [{"message": {"content": '{"values": [1]}'}}]}
            )

        client = OpenAICompatibleClient(settings(LLMProvider.OPENAI_COMPATIBLE))
        schema = {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "maxItems": 3,
                    "uniqueItems": True,
                }
            },
            "required": ["values"],
            "additionalProperties": False,
        }
        with patch.object(client._opener, "open", side_effect=fake_open):
            result = client.generate_json(
                system_prompt="system",
                user_prompt="user",
                response_schema=schema,
            )

        body = json.loads(requests[0].data)
        projected = body["response_format"]["json_schema"]["schema"]
        self.assertNotIn("uniqueItems", projected["properties"]["values"])
        self.assertEqual(projected["properties"]["values"]["minItems"], 1)
        self.assertEqual(projected["properties"]["values"]["maxItems"], 3)
        self.assertEqual(result.payload, {"values": [1]})

    def test_both_adapters_return_the_same_generation_contract(self) -> None:
        responses = [
            FakeResponse({"message": {"content": '{"value": 7}'}}),
            FakeResponse({"choices": [{"message": {"content": '{"value": 7}'}}]}),
        ]
        ollama_client = OllamaClient(settings(LLMProvider.OLLAMA))
        openai_client = OpenAICompatibleClient(
            settings(LLMProvider.OPENAI_COMPATIBLE)
        )
        with patch.object(
            ollama_client._opener, "open", return_value=responses[0]
        ), patch.object(openai_client._opener, "open", return_value=responses[1]):
            ollama = ollama_client.generate_json(
                system_prompt="system", user_prompt="user", response_schema=SCHEMA
            )
            openai = openai_client.generate_json(
                system_prompt="system", user_prompt="user", response_schema=SCHEMA
            )
        self.assertEqual(ollama.payload, openai.payload)
        self.assertEqual(ollama.model, openai.model)

    def test_malformed_content_empty_choices_and_size_are_rejected(self) -> None:
        client = OllamaClient(settings(LLMProvider.OLLAMA, max_retries=0))
        with patch.object(
            client._opener,
            "open",
            return_value=FakeResponse({"message": {"content": "not-json"}}),
        ), self.assertRaises(LLMResponseError) as raised:
            client.generate_json(
                system_prompt="system", user_prompt="user", response_schema=SCHEMA
            )
        self.assertEqual(raised.exception.code, "LLM_INVALID_JSON")

        openai = OpenAICompatibleClient(
            settings(LLMProvider.OPENAI_COMPATIBLE, max_retries=0)
        )
        with patch.object(
            openai._opener, "open", return_value=FakeResponse({"choices": []})
        ), self.assertRaises(LLMResponseError) as raised:
            openai.generate_json(
                system_prompt="system", user_prompt="user", response_schema=SCHEMA
            )
        self.assertEqual(raised.exception.code, "LLM_CHOICES_MISSING")
        with patch.object(
            openai._opener,
            "open",
            return_value=FakeResponse(
                {"choices": [{"message": {"content": "not-json"}}]}
            ),
        ), self.assertRaises(LLMResponseError) as raised:
            openai.generate_json(
                system_prompt="system", user_prompt="user", response_schema=SCHEMA
            )
        self.assertEqual(raised.exception.code, "LLM_INVALID_JSON")

        for oversized_client in (client, openai):
            with self.subTest(client=type(oversized_client).__name__), patch.object(
                oversized_client._opener,
                "open",
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
            with self.subTest(client=type(client).__name__), patch.object(
                client._opener, "open", side_effect=socket.timeout("prompt payload")
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
            with patch.object(
                client._opener, "open", side_effect=error
            ), self.assertRaises(LLMResponseError) as raised:
                client.generate_json(
                    system_prompt="private system",
                    user_prompt="private user",
                    response_schema=SCHEMA,
                )
            self.assertEqual(raised.exception.code, "LLM_HTTP_ERROR")
            self.assertNotIn("never-log-this-key", str(raised.exception))
            self.assertNotIn("private user", str(raised.exception))

    def test_all_redirects_are_rejected_without_following_or_retrying(self) -> None:
        cases = (
            (LLMProvider.OLLAMA, None),
            (LLMProvider.OPENAI_COMPATIBLE, None),
            (LLMProvider.OPENAI_COMPATIBLE, "synthetic-bearer-token"),
        )
        for provider, api_key in cases:
            for status_code in sorted(client_module.HTTP_REDIRECT_STATUS_CODES):
                with self.subTest(
                    provider=provider.value,
                    token=bool(api_key),
                    status=status_code,
                ):
                    client = create_llm_client(
                        settings(provider, api_key=api_key, max_retries=1)
                    )
                    redirect_handlers = [
                        handler
                        for handler in client._opener.handlers
                        if isinstance(handler, urllib.request.HTTPRedirectHandler)
                    ]
                    self.assertEqual(
                        [type(handler) for handler in redirect_handlers],
                        [client_module._RejectAllRedirects],
                    )
                    transport = MockRedirectTransport(status_code)
                    client._opener = transport
                    with self.assertRaises(LLMResponseError) as raised:
                        client.generate_json(
                            system_prompt="private-system-prompt",
                            user_prompt="private-user-prompt",
                            response_schema=SCHEMA,
                        )

                    self.assertEqual(
                        raised.exception.code, "LLM_HTTP_REDIRECT_REJECTED"
                    )
                    self.assertEqual(len(transport.initial_requests), 1)
                    self.assertEqual(transport.destination_requests, [])
                    self.assertEqual(transport.destination_authorizations, [])
                    self.assertEqual(transport.destination_bodies, [])
                    initial = transport.initial_requests[0]
                    self.assertEqual(initial.full_url, client.settings.endpoint)
                    if api_key:
                        self.assertEqual(
                            initial.get_header("Authorization"),
                            "Bearer synthetic-bearer-token",
                        )
                    else:
                        self.assertIsNone(initial.get_header("Authorization"))
                    safe_error = str(raised.exception)
                    self.assertIn(provider.value, safe_error)
                    self.assertIn("3xx", safe_error)
                    self.assertIn("not retried", safe_error)
                    for secret in (
                        "synthetic-bearer-token",
                        "private-system-prompt",
                        "private-user-prompt",
                        "outside.invalid",
                        "secret-target",
                        "synthetic redirect",
                    ):
                        self.assertNotIn(secret, safe_error)

    def test_redirect_http_error_defense_does_not_retry(self) -> None:
        client = OpenAICompatibleClient(
            settings(
                LLMProvider.OPENAI_COMPATIBLE,
                api_key="synthetic-bearer-token",
                max_retries=1,
            )
        )
        error = urllib.error.HTTPError(
            client.settings.endpoint,
            308,
            "private response body and Location",
            {"Location": "http://outside.invalid/secret-target"},
            None,
        )
        with patch.object(
            client._opener, "open", side_effect=error
        ) as request, self.assertRaises(LLMResponseError) as raised:
            client.generate_json(
                system_prompt="private-system-prompt",
                user_prompt="private-user-prompt",
                response_schema=SCHEMA,
            )
        self.assertEqual(request.call_count, 1)
        self.assertEqual(raised.exception.code, "LLM_HTTP_REDIRECT_REJECTED")
        for secret in (
            "synthetic-bearer-token",
            "private-system-prompt",
            "private-user-prompt",
            "outside.invalid",
            "private response body",
        ):
            self.assertNotIn(secret, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
