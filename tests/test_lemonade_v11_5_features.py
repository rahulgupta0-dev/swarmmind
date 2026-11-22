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

from swarmmind.config import Config, ModelsConfig, WebConfig
import swarmmind.lemonade.client as lemonade_client_module
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


class TestOmniConfigDefaults:
    """Config additions for the Omni Model path and web page fetching."""

    def test_models_config_omni_defaults_to_empty(self) -> None:
        assert ModelsConfig().omni == ""

    def test_models_config_accepts_omni_collection(self) -> None:
        cfg = ModelsConfig(omni="LMX-Omni-5.5B-Lite")
        assert cfg.omni == "LMX-Omni-5.5B-Lite"

    def test_web_config_defaults(self) -> None:
        web = WebConfig()
        assert web.fetch_pages is True
        assert web.max_pages == 5
        assert web.timeout_seconds == 5.0

    def test_config_exposes_web_default(self) -> None:
        cfg = Config()
        assert isinstance(cfg.web, WebConfig)
        assert cfg.web.fetch_pages is True
        assert cfg.web.max_pages == 5
        assert cfg.web.timeout_seconds == 5.0

    def test_config_backward_compatible_with_unknown_fields(self) -> None:
        """Old config files with no [web]/omni section still load; extras ignored."""
        cfg = Config(models={"worker": "Old-Model"}, legacy_section={"x": 1})  # type: ignore[call-arg]
        assert cfg.models.worker == "Old-Model"
        assert cfg.models.omni == ""
        assert cfg.web.fetch_pages is True


class TestIsOmniModel:
    """is_omni_model recognises Omni collections and rejects ordinary models."""

    def test_cases(self) -> None:
        client = LemonadeClient("http://localhost:13305")
        assert client.is_omni_model("LMX-Omni-5.5B-Lite") is True
        assert client.is_omni_model("my-omni-collection") is True
        assert client.is_omni_model("OMNI-7B") is True
        assert client.is_omni_model("Qwen3.6-35B-A3B-GGUF") is False
        assert client.is_omni_model("Flux-2-Klein-4B") is False
        assert client.is_omni_model("") is False
        assert client.is_omni_model(None) is False  # type: ignore[arg-type]


class TestOmniClientMethods:
    """Mocked-HTTP coverage for the new Omni client methods."""

    def _capture(
        self,
        client: LemonadeClient,
        monkeypatch: pytest.MonkeyPatch,
        response: httpx.Response,
        captured: dict[str, Any],
    ) -> None:
        async def mock_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
            captured["method"] = method
            captured["path"] = path
            captured["kwargs"] = kwargs
            return response

        monkeypatch.setattr(client, "_request", mock_request)

    def test_generate_image_uses_config_default_model_and_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = LemonadeClient("http://localhost:13305")
        captured: dict[str, Any] = {}
        self._capture(client, monkeypatch, httpx.Response(200, json={"data": [{"url": "x"}]}), captured)

        result = asyncio.run(client.generate_image("a cat"))

        assert result["data"][0]["url"] == "x"
        assert captured["method"] == "POST"
        assert captured["path"] == "/v1/images/generations"
        assert captured["kwargs"]["json"]["model"] == ModelsConfig().image
        assert captured["kwargs"]["json"]["prompt"] == "a cat"
        assert captured["kwargs"]["json"]["size"] == "1024x1024"

    def test_generate_image_passes_omni_model_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = LemonadeClient("http://localhost:13305")
        captured: dict[str, Any] = {}
        self._capture(client, monkeypatch, httpx.Response(200, json={"data": []}), captured)

        asyncio.run(client.generate_image("cat", model="LMX-Omni-5.5B-Lite", size="512x512"))

        assert captured["kwargs"]["json"]["model"] == "LMX-Omni-5.5B-Lite"
        assert captured["kwargs"]["json"]["size"] == "512x512"

    def test_generate_image_uses_omni_from_config_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            lemonade_client_module,
            "_DEFAULT_MODELS",
            ModelsConfig(omni="LMX-Omni-5.5B-Lite"),
        )
        client = LemonadeClient("http://localhost:13305")
        captured: dict[str, Any] = {}
        self._capture(client, monkeypatch, httpx.Response(200, json={"data": []}), captured)

        asyncio.run(client.generate_image("cat"))

        assert captured["kwargs"]["json"]["model"] == "LMX-Omni-5.5B-Lite"

    def test_edit_image(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = LemonadeClient("http://localhost:13305")
        captured: dict[str, Any] = {}
        self._capture(client, monkeypatch, httpx.Response(200, json={"data": [{"b64_json": "YWJj"}]}), captured)

        result = asyncio.run(client.edit_image("YWJj", "make it blue", model="Flux-2-Klein-4B"))

        assert result["data"][0]["b64_json"] == "YWJj"
        assert captured["path"] == "/v1/images/edits"
        assert captured["kwargs"]["json"]["image"] == "YWJj"
        assert captured["kwargs"]["json"]["prompt"] == "make it blue"
        assert captured["kwargs"]["json"]["model"] == "Flux-2-Klein-4B"

    def test_text_to_speech_returns_bytes_and_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = LemonadeClient("http://localhost:13305")
        captured: dict[str, Any] = {}
        self._capture(client, monkeypatch, httpx.Response(200, content=b"RIFFaudio"), captured)

        audio = asyncio.run(client.text_to_speech("hello world"))

        assert audio == b"RIFFaudio"
        assert captured["path"] == "/v1/audio/speech"
        assert captured["kwargs"]["json"] == {
            "model": ModelsConfig().tts,
            "input": "hello world",
            "voice": "af",
        }

    def test_text_to_speech_with_omni_and_custom_voice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = LemonadeClient("http://localhost:13305")
        captured: dict[str, Any] = {}
        self._capture(client, monkeypatch, httpx.Response(200, content=b"audio"), captured)

        asyncio.run(client.text_to_speech("hi", model="LMX-Omni-5.5B-Lite", voice="af_hex"))

        assert captured["kwargs"]["json"]["model"] == "LMX-Omni-5.5B-Lite"
        assert captured["kwargs"]["json"]["voice"] == "af_hex"

    def test_transcribe_audio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = LemonadeClient("http://localhost:13305")
        captured: dict[str, Any] = {}
        self._capture(client, monkeypatch, httpx.Response(200, json={"text": "hello"}), captured)

        result = asyncio.run(client.transcribe_audio(b"audio-bytes", model="LMX-Omni-5.5B-Lite"))

        assert result == {"text": "hello"}
        assert captured["path"] == "/v1/audio/transcriptions"
        assert captured["kwargs"]["data"]["model"] == "LMX-Omni-5.5B-Lite"
        assert captured["kwargs"]["files"]["file"][1] == b"audio-bytes"

    def test_analyze_image_sends_vision_chat_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = LemonadeClient("http://localhost:13305")
        captured: dict[str, Any] = {}
        self._capture(
            client,
            monkeypatch,
            httpx.Response(200, json={"choices": [{"message": {"content": "a cat"}}]}),
            captured,
        )

        result = asyncio.run(client.analyze_image("YWJj", "what is this?", model="LMX-Omni-5.5B-Lite"))

        assert result["choices"][0]["message"]["content"] == "a cat"
        assert captured["path"] == "/v1/chat/completions"
        body = captured["kwargs"]["json"]
        assert body["model"] == "LMX-Omni-5.5B-Lite"
        content = body["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "what is this?"}
        assert content[1]["image_url"]["url"] == "data:image/png;base64,YWJj"

    def test_omni_methods_propagate_http_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Omni methods reuse _request, so transport/HTTP errors raise as usual."""
        client = LemonadeClient("http://localhost:13305")

        async def error_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
            req = httpx.Request("POST", "http://localhost:13305/v1/images/generations")
            resp = httpx.Response(500, request=req)
            raise httpx.HTTPStatusError("500", request=req, response=resp)

        monkeypatch.setattr(client, "_request", error_request)

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(client.generate_image("cat"))
