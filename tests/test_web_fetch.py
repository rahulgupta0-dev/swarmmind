"""Tests for bounded, honest web page fetching (:func:`fetch_pages`)."""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from swarmmind.core import workers
from swarmmind.core.workers import fetch_pages


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[str]:
    """Route the client created inside ``fetch_pages`` through a mock transport."""
    calls: list[str] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    transport = httpx.MockTransport(recording_handler)
    real_client = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr(workers.httpx, "AsyncClient", factory)
    return calls


@pytest.mark.asyncio
async def test_fetch_pages_success_extracts_text_and_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_transport(monkeypatch, lambda request: httpx.Response(
        200,
        text="<html><head><title>Alpha Page</title></head>"
             "<body><script>bad()</script><h1>Hello</h1> <p>world</p></body></html>",
        request=request,
    ))

    result = await fetch_pages(
        ["https://example.com/a"],
        max_pages=5,
        timeout_seconds=1.0,
    )

    assert len(result) == 1
    page = result[0]
    assert page["kind"] == "web"
    assert page["fetch_ok"] is True
    assert page["title"] == "Alpha Page"
    assert "Hello" in page["snippet"] and "world" in page["snippet"]
    assert "bad()" not in page["snippet"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_fetch_pages_failure_uses_ddg_snippet_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    _patch_transport(monkeypatch, failing)

    result = await fetch_pages(
        [{
            "url": "https://down.example/page",
            "title": "DDG title",
            "snippet": "DDG snippet kept as fallback",
        }],
        max_pages=5,
        timeout_seconds=1.0,
    )

    assert len(result) == 1
    assert result[0]["fetch_ok"] is False
    assert result[0]["snippet"] == "DDG snippet kept as fallback"
    assert result[0]["url"] == "https://down.example/page"


@pytest.mark.asyncio
async def test_fetch_pages_rejects_non_http_schemes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_transport(monkeypatch, lambda request: httpx.Response(
        200, text="should never be requested", request=request,
    ))

    result = await fetch_pages(
        ["file:///etc/passwd", "javascript:alert(1)", "ftp://example.com/x"],
        max_pages=5,
        timeout_seconds=1.0,
    )

    assert len(result) == 3
    assert all(page["fetch_ok"] is False for page in result)
    assert all(page["kind"] == "web" for page in result)
    # Security guard: no request was made for any non-http(s) URL.
    assert calls == []


@pytest.mark.asyncio
async def test_fetch_pages_respects_max_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_transport(monkeypatch, lambda request: httpx.Response(
        200, text="<html><body>ok</body></html>", request=request,
    ))

    urls = [f"https://example.com/{i}" for i in range(6)]
    result = await fetch_pages(urls, max_pages=2, timeout_seconds=1.0)

    assert len(result) == 2
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_fetch_pages_zero_max_pages_returns_empty() -> None:
    assert await fetch_pages(
        ["https://example.com"], max_pages=0, timeout_seconds=1.0,
    ) == []


@pytest.mark.asyncio
async def test_fetch_pages_caps_snippet_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = "<html><body>" + ("word " * 10_000) + "</body></html>"
    _patch_transport(monkeypatch, lambda request: httpx.Response(
        200, text=body, request=request,
    ))

    result = await fetch_pages(
        ["https://example.com/big"], max_pages=1, timeout_seconds=1.0,
    )

    assert result[0]["fetch_ok"] is True
    assert len(result[0]["snippet"]) <= 1200
