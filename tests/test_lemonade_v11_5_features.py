"""TDD unit tests for Lemonade v11.5.0 updates in SwarmMind.

Covers:
1. Model busy detection: parse is_busy / is_streaming from /v1/health
2. Error Diagnostics: handle 403 Forbidden origin errors in health checks and orchestrator
3. Router / Classifier Integration: ModelsConfig router support & LemonadeClient POST /v1/classify
"""

from __future__ import annotations

import asyncio
from typing import Any
import pytest
import httpx

from swarmmind.config import Config, ModelsConfig
from swarmmind.lemonade.client import LemonadeClient


class TestModelBusyDetection:
    """Test parsing is_busy and is_streaming from /v1/health endpoint."""

    def test_health_check_parses_model_busy_and_streaming_states(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """health_check should parse 'is_busy', 'is_streaming', and 'models' array."""
        client = LemonadeClient("http://localhost:13305")

        mock_health_response = {
            "status": "ok",
            "models": [
                {"name": "Qwen3.6-35B-A3B-GGUF", "is_busy": True, "is_streaming": False},
                {"name": "Gemma-4-12B-it", "is_busy": False, "is_streaming": True},
            ],
            "is_busy": True,
            "is_streaming": True,
        }

        async def mock_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
            assert path == "/v1/health"
            return httpx.Response(200, json=mock_health_response)

        monkeypatch.setattr(client, "_request", mock_request)

        res = asyncio.run(client.health_check())

        assert res["status"] == "ok"
        assert res["is_busy"] is True
        assert res["is_streaming"] is True
        assert len(res["models"]) == 2
        assert res["models"][0]["is_busy"] is True

    def test_get_model_busy_status_specific_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_model_busy_status returns status for target model."""
        client = LemonadeClient("http://localhost:13305")

        mock_health_response = {
            "status": "ok",
            "models": [
                {"name": "Qwen3.6-35B-A3B-GGUF", "is_busy": True, "is_streaming": False},
                {"name": "Gemma-4-12B-it", "is_busy": False, "is_streaming": False},
            ],
        }

        async def mock_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
            return httpx.Response(200, json=mock_health_response)

        monkeypatch.setattr(client, "_request", mock_request)

        status_qwen = asyncio.run(client.get_model_busy_status("Qwen3.6-35B-A3B-GGUF"))
        assert status_qwen == {"is_busy": True, "is_streaming": False}

        status_gemma = asyncio.run(client.get_model_busy_status("Gemma-4-12B-it"))
        assert status_gemma == {"is_busy": False, "is_streaming": False}

        status_unknown = asyncio.run(client.get_model_busy_status("nonexistent"))
        assert status_unknown == {"is_busy": False, "is_streaming": False}


class TestErrorDiagnostics403:
    """Test 403 Forbidden origin rejection diagnostics."""

    def test_health_check_returns_forbidden_status_on_403(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """health_check should return status='forbidden' with clear advice on HTTP 403."""
        client = LemonadeClient("http://localhost:13305")

        async def mock_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
            req = httpx.Request("GET", "http://localhost:13305/v1/health")
            resp = httpx.Response(403, content=b"Forbidden: Origin not allowed", request=req)
            raise httpx.HTTPStatusError("403 Forbidden", request=req, response=resp)

        monkeypatch.setattr(client, "_request", mock_request)

        res = asyncio.run(client.health_check())

        assert res["status"] == "forbidden"
        assert res["code"] == 403
        assert "LEMONADE_ALLOWED_ORIGINS" in res["detail"]

    def test_orchestrator_raises_permission_error_with_403_advice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Orchestrator.run should raise PermissionError with LEMONADE_ALLOWED_ORIGINS advice on 403."""
        from swarmmind.core.orchestrator import Orchestrator

        config = Config()
        client = LemonadeClient("http://localhost:13305")

        async def mock_health_check() -> dict[str, Any]:
            return {
                "status": "forbidden",
                "code": 403,
                "detail": "403 Forbidden: Origin rejected by Lemonade server. Ensure LEMONADE_ALLOWED_ORIGINS includes your client origin.",
            }

        monkeypatch.setattr(client, "health_check", mock_health_check)
        orchestrator = Orchestrator(config, client)

        with pytest.raises(PermissionError) as exc_info:
            asyncio.run(orchestrator.run("Test query"))

        assert "LEMONADE_ALLOWED_ORIGINS" in str(exc_info.value)


class TestRouterAndClassifierIntegration:
    """Test Lemonade Router and /v1/classify features."""

    def test_models_config_supports_router_model(self) -> None:
        """ModelsConfig should support router role and is_router_model helper."""
        cfg = ModelsConfig(
            router="routing.router",
            conductor="my_collection.router",
        )
        assert cfg.router == "routing.router"
        assert cfg.is_router_model("routing.router") is True
        assert cfg.is_router_model("my_collection.router") is True
        assert cfg.is_router_model("Gemma-4-12B-it") is False

    def test_client_classify_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """client.classify should call POST /v1/classify and return response dict."""
        client = LemonadeClient("http://localhost:13305")

        mock_classify_response = {
            "model": "routing.router",
            "results": [
                {"label": "rag", "score": 0.92},
                {"label": "web", "score": 0.08},
            ],
        }

        async def mock_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
            assert method == "POST"
            assert path == "/v1/classify"
            assert kwargs["json"]["model"] == "routing.router"
            assert kwargs["json"]["text"] == "Search vector database"
            return httpx.Response(200, json=mock_classify_response)

        monkeypatch.setattr(client, "_request", mock_request)

        res = asyncio.run(client.classify("Search vector database", model="routing.router"))
        assert res["model"] == "routing.router"
        assert len(res["results"]) == 2
        assert res["results"][0]["label"] == "rag"
