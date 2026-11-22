"""TDD tests for LemonadeClient — prove and fix bugs.

Tests:
1. _stream_chat references self._client which doesn't exist
2. close() is a no-op but should be safe
3. async context manager works
"""

from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest


class TestLemonadeClientStreamBug:
    """Prove _stream_chat has a self._client AttributeError."""

    def test_stream_chat_method_exists(self) -> None:
        """_stream_chat should be a method on LemonadeClient."""
        from swarmmind.lemonade.client import LemonadeClient

        assert hasattr(LemonadeClient, "_stream_chat")

    def test_stream_chat_references_nonexistent_self_client(self) -> None:
        """_stream_chat references self._client which is never set.

        This test proves the bug by inspecting the source code.
        After fix, _stream_chat should use self._request or self.base_url.
        """
        from swarmmind.lemonade.client import LemonadeClient

        source = inspect.getsource(LemonadeClient._stream_chat)

        # The buggy version references self._client.stream(...)
        # The fixed version should use self._request or httpx directly
        has_self_client = "self._client" in source
        has_self_request = "self._request" in source or "self.base_url" in source

        if has_self_client and not has_self_request:
            pytest.fail(
                "_stream_chat references self._client which is never set. "
                "Should use self._request or httpx.AsyncClient directly."
            )

    def test_init_does_not_set_client_attribute(self) -> None:
        """LemonadeClient.__init__ should NOT set self._client.

        This proves the bug: _stream_chat uses self._client but __init__ never creates it.
        """
        from swarmmind.lemonade.client import LemonadeClient

        client = LemonadeClient("http://localhost:13305")
        assert not hasattr(client, "_client"), (
            "LemonadeClient should not have a _client attribute — "
            "each request creates its own httpx.AsyncClient"
        )

    def test_chat_completion_stream_false_works(self) -> None:
        """chat_completion with stream=False should work (no streaming bug)."""
        from swarmmind.lemonade.client import LemonadeClient

        client = LemonadeClient("http://localhost:13305")
        # Just verify the method exists and is callable
        assert callable(client.chat_completion)

    def test_stream_chat_uses_async_client_correctly(self) -> None:
        """After fix, _stream_chat should create its own httpx.AsyncClient.

        Verify the method body creates a new client or uses self._request.
        """
        from swarmmind.lemonade.client import LemonadeClient

        source = inspect.getsource(LemonadeClient._stream_chat)

        # Should NOT reference self._client (the buggy pattern)
        # Should either:
        #   a) Use self._request (preferred), or
        #   b) Create a new httpx.AsyncClient within the method
        uses_self_client = "self._client" in source
        uses_new_client = "httpx.AsyncClient" in source
        uses_request = "self._request" in source

        assert not uses_self_client or uses_new_client or uses_request, (
            "_stream_chat still references self._client without alternatives"
        )


class TestLemonadeClientSafeOperations:
    """Verify client operations are safe and don't crash."""

    def test_close_is_safe(self) -> None:
        """close() should not raise even on a fresh client."""
        from swarmmind.lemonade.client import LemonadeClient

        client = LemonadeClient("http://localhost:13305")
        # Should not raise
        import asyncio
        asyncio.run(client.close())

    def test_async_context_manager(self) -> None:
        """async with should work."""
        from swarmmind.lemonade.client import LemonadeClient

        async def _test():
            async with LemonadeClient("http://localhost:13305") as client:
                assert client.base_url == "http://localhost:13305"

        asyncio.run(_test())

    def test_health_check_returns_error_gracefully(self) -> None:
        """health_check should not raise when server is unreachable."""
        from swarmmind.lemonade.client import LemonadeClient

        client = LemonadeClient("http://127.0.0.1:1")
        result = asyncio.run(client.health_check())
        assert result["status"] == "error"

    def test_safe_request_json_returns_empty_on_failure(self) -> None:
        """_safe_request_json should return {} on failure, not raise."""
        from swarmmind.lemonade.client import LemonadeClient

        client = LemonadeClient("http://127.0.0.1:1")
        result = asyncio.run(client._safe_request_json("GET", "/v1/stats"))
        assert result == {}


class TestLemonadeClientStreamingFixed:
    """Tests that prove streaming works after the fix."""

    def test_stream_chat_creates_client_in_context(self) -> None:
        """_stream_chat should create its own httpx.AsyncClient."""
        from swarmmind.lemonade.client import LemonadeClient

        source = inspect.getsource(LemonadeClient._stream_chat)
        # After fix: should use async with httpx.AsyncClient(...) or similar
        assert "AsyncClient" in source or "self._request" in source, (
            "_stream_chat should create its own AsyncClient or use self._request"
        )

    def test_streaming_not_broken_by_missing_attribute(self) -> None:
        """Calling _stream_chat should not raise AttributeError on self._client."""
        from swarmmind.lemonade.client import LemonadeClient

        client = LemonadeClient("http://127.0.0.1:1", timeout=0.1)
        # This should raise a connection error, NOT an AttributeError
        with pytest.raises(Exception) as exc_info:
            asyncio.run(client._stream_chat({"model": "test", "messages": []}))

        # Should NOT be AttributeError
        assert not isinstance(exc_info.value, AttributeError), (
            f"_stream_chat raised AttributeError (self._client bug): {exc_info.value}"
        )


class TestLemonadeAuthHeaders:
    """Lemonade v2026.47+ API-key support via environment variables."""

    def test_auth_header_present_when_env_key_set(self, monkeypatch: Any) -> None:
        from swarmmind.lemonade.client import LemonadeClient

        monkeypatch.setenv("LEMONADE_API_KEY", "secret-key")
        monkeypatch.delenv("LEMONADE_ADMIN_API_KEY", raising=False)

        client = LemonadeClient("http://localhost:13305")
        headers = client._make_client().headers
        assert headers.get("Authorization") == "Bearer secret-key"

    def test_auth_header_absent_when_no_env_key(self, monkeypatch: Any) -> None:
        from swarmmind.lemonade.client import LemonadeClient

        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        monkeypatch.delenv("LEMONADE_ADMIN_API_KEY", raising=False)

        client = LemonadeClient("http://localhost:13305")
        assert "Authorization" not in client._make_client().headers

    def test_admin_key_fallback(self, monkeypatch: Any) -> None:
        from swarmmind.lemonade.client import LemonadeClient

        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        monkeypatch.setenv("LEMONADE_ADMIN_API_KEY", "admin-key")

        client = LemonadeClient("http://localhost:13305")
        assert client._make_client().headers.get("Authorization") == "Bearer admin-key"


class TestLemonadeFriendlyErrors:
    """Actionable HTTP 400 messages for missing-model / non-LLM errors."""

    def _patch_400(self, monkeypatch: Any, error_body: str) -> None:
        request = httpx.Request("POST", "http://localhost:13305/v1/chat/completions")
        response = httpx.Response(400, json={"error": error_body}, request=request)

        async def fake_request(self: Any, method: str, url: str, **kwargs: Any) -> Any:
            raise httpx.HTTPStatusError(
                "Client error 400", request=request, response=response,
            )

        monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    @pytest.mark.asyncio
    async def test_friendly_missing_model_400(self, monkeypatch: Any) -> None:
        from swarmmind.lemonade.client import LemonadeClient

        self._patch_400(monkeypatch, "Missing model field in request")
        client = LemonadeClient("http://localhost:13305")

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.chat_completion(
                model="x", messages=[{"role": "user", "content": "hi"}],
            )

        message = str(exc_info.value)
        assert "HTTP 400" in message
        assert "Hint:" in message
        assert "model" in message.lower()

    @pytest.mark.asyncio
    async def test_friendly_non_llm_400(self, monkeypatch: Any) -> None:
        from swarmmind.lemonade.client import LemonadeClient

        self._patch_400(monkeypatch, "Model my-embed is not an LLM")
        client = LemonadeClient("http://localhost:13305")

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.chat_completion(
                model="my-embed", messages=[{"role": "user", "content": "hi"}],
            )

        message = str(exc_info.value)
        assert "not an LLM" in message
        assert "Hint:" in message


def test_version_is_0_4_0() -> None:
    from swarmmind import __version__

    assert __version__ == "0.4.0"


class TestLemonadeConfigApiKey:
    """Wiring of the optional config-file api_key (Lemonade v2026.47.1+)."""

    def test_config_field_defaults_to_empty(self) -> None:
        from swarmmind.config import Config, LemonadeConfig

        assert LemonadeConfig().api_key == ""
        assert Config().lemonade.api_key == ""

    def test_config_api_key_reaches_client_and_all_call_sites(self, monkeypatch: Any) -> None:
        from pathlib import Path

        import swarmmind
        from swarmmind.config import Config
        from swarmmind.lemonade.client import LemonadeClient

        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        monkeypatch.delenv("LEMONADE_ADMIN_API_KEY", raising=False)

        config = Config()
        config.lemonade.api_key = "config-secret"
        client = LemonadeClient(
            config.get_lemonade_base_url(),
            api_key=config.lemonade.api_key or None,
        )
        assert client._make_client().headers.get("Authorization") == "Bearer config-secret"

        # All three production construction sites must thread the config value.
        pkg = Path(swarmmind.__file__).parent
        for rel in ("cli/main.py", "ui/app.py", "benchmark.py"):
            source = (pkg / rel).read_text(encoding="utf-8")
            assert "api_key=config.lemonade.api_key or None" in source, (
                f"{rel} does not thread config.lemonade.api_key"
            )

    def test_explicit_api_key_takes_precedence_over_env(self, monkeypatch: Any) -> None:
        from swarmmind.lemonade.client import LemonadeClient

        monkeypatch.setenv("LEMONADE_API_KEY", "env-key")
        monkeypatch.delenv("LEMONADE_ADMIN_API_KEY", raising=False)

        explicit = LemonadeClient("http://localhost:13305", api_key="explicit-key")
        assert explicit._make_client().headers.get("Authorization") == "Bearer explicit-key"

        # An empty/None explicit key still allows the env-var fallback.
        fallback = LemonadeClient("http://localhost:13305", api_key=None)
        assert fallback._make_client().headers.get("Authorization") == "Bearer env-key"

    def test_no_auth_header_when_no_config_or_env_key(self, monkeypatch: Any) -> None:
        from swarmmind.config import Config
        from swarmmind.lemonade.client import LemonadeClient

        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        monkeypatch.delenv("LEMONADE_ADMIN_API_KEY", raising=False)

        config = Config()
        assert config.lemonade.api_key == ""
        client = LemonadeClient(
            config.get_lemonade_base_url(),
            api_key=config.lemonade.api_key or None,
        )
        assert "Authorization" not in client._make_client().headers


class TestStreamOptionsAndUsage:
    """FLM 1.0.10+ opt-in usage (Lemonade v2026.46.2, PR #4037)."""

    @staticmethod
    def _patch_transport(
        monkeypatch: Any,
        lines: list[str],
        captured: dict[str, Any],
    ) -> None:
        class FakeStreamResponse:
            def raise_for_status(self) -> None:
                pass

            async def aiter_lines(self) -> Any:
                for line in lines:
                    yield line

        @asynccontextmanager
        async def fake_stream(self: Any, method: str, url: str, **kwargs: Any) -> Any:
            captured["stream"] = kwargs.get("json")
            yield FakeStreamResponse()

        class FakeResponse:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, Any]:
                return {"ok": True}

        async def fake_request(self: Any, method: str, url: str, **kwargs: Any) -> Any:
            captured["request"] = kwargs.get("json")
            return FakeResponse()

        monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
        monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    @pytest.mark.asyncio
    async def test_stream_options_present_only_for_streaming(self, monkeypatch: Any) -> None:
        from swarmmind.lemonade.client import LemonadeClient

        captured: dict[str, Any] = {}
        self._patch_transport(monkeypatch, ["data: [DONE]"], captured)
        client = LemonadeClient("http://localhost:13305")
        messages = [{"role": "user", "content": "hi"}]

        stream_gen = await client.chat_completion("m", messages, stream=True)
        assert [chunk async for chunk in stream_gen] == []
        assert captured["stream"]["stream_options"] == {"include_usage": True}
        assert captured["stream"]["stream"] is True

        await client.chat_completion("m", messages, stream=False)
        assert "stream_options" not in captured["request"]

    @pytest.mark.asyncio
    async def test_usage_chunk_populates_last_usage_without_being_yielded(
        self, monkeypatch: Any,
    ) -> None:
        from swarmmind.lemonade.client import LemonadeClient

        content_chunk = {
            "choices": [{"delta": {"content": "Hello"}, "finish_reason": None}],
        }
        usage_chunk = {
            "choices": [],
            "usage": {
                "completion_tokens": 7,
                "prompt_tokens": 3,
                "total_tokens": 10,
            },
        }
        lines = [
            f"data: {json.dumps(content_chunk)}",
            f"data: {json.dumps(usage_chunk)}",
            "data: [DONE]",
        ]
        captured: dict[str, Any] = {}
        self._patch_transport(monkeypatch, lines, captured)
        client = LemonadeClient("http://localhost:13305")
        assert client.last_usage == {}

        gen = await client.chat_completion(
            "m", [{"role": "user", "content": "hi"}], stream=True,
        )
        chunks = [chunk async for chunk in gen]

        assert chunks == [content_chunk]
        assert client.last_usage["completion_tokens"] == 7
        assert client.last_usage["prompt_tokens"] == 3
        assert client.last_usage["total_tokens"] == 10

    @pytest.mark.asyncio
    async def test_consuming_usage_resets_it(self, monkeypatch: Any) -> None:
        from swarmmind.lemonade.client import LemonadeClient

        usage_chunk = {
            "usage": {
                "completion_tokens": 5,
                "prompt_tokens": 2,
                "total_tokens": 7,
            },
        }
        captured: dict[str, Any] = {}
        self._patch_transport(
            monkeypatch,
            [f"data: {json.dumps(usage_chunk)}", "data: [DONE]"],
            captured,
        )
        client = LemonadeClient("http://localhost:13305")
        gen = await client.chat_completion(
            "m", [{"role": "user", "content": "hi"}], stream=True,
        )
        assert [chunk async for chunk in gen] == []
        assert client.consume_last_usage()["completion_tokens"] == 5
        assert client.last_usage == {}

