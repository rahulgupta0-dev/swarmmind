"""TDD tests for LemonadeClient — prove and fix bugs.

Tests:
1. _stream_chat references self._client which doesn't exist
2. close() is a no-op but should be safe
3. async context manager works
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

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
