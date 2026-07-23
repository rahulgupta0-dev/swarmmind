"""OpenAI-compatible HTTP client for AMD Lemonade."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60.0


class LemonadeClient:
    """Thin async HTTP client wrapping AMD Lemonade's OpenAI-compatible API.

    Args:
        base_url: The Lemonade server base URL (e.g. ``http://localhost:13305``).
        api_key: API key (Lemonade typically doesn't require one).
        timeout: Default request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "not-needed",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Internal: low-level request with graceful degradation
    # ------------------------------------------------------------------

    def _make_client(self) -> httpx.AsyncClient:
        """Create a configured ``httpx.AsyncClient`` for a single request.

        Centralised here so both :meth:`_request` and :meth:`_stream_chat`
        share identical timeout, header, and base-URL settings.
        """
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self._timeout),
            headers={"Authorization": f"Bearer {self.api_key}"},
        )

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue an HTTP request, raising on transport/HTTP errors."""
        url = f"{self.base_url}{path}"
        logger.debug("%s %s", method, url)
        try:
            async with self._make_client() as client:
                response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            logger.error("HTTP error %s: %s", exc.response.status_code, exc.response.text)
            raise
        except httpx.RequestError as exc:
            logger.error("Request failed: %s", exc)
            raise

    async def _safe_request_json(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Issue an HTTP request, parse the JSON body; return ``{}`` on any failure.

        Designed for *informational* endpoints (hardware, stats) where a
        missing or unreachable endpoint must not crash the calling code.
        """
        try:
            resp = await self._request(method, path, **kwargs)
            data = resp.json()
            if not isinstance(data, dict):
                # Some servers wrap responses in lists; normalise.
                return {"data": data}
            return data
        except Exception as exc:
            logger.warning("Safe request %s %s failed: %s", method, path, exc)
            return {}

    # ------------------------------------------------------------------
    # Chat Completions
    # ------------------------------------------------------------------

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any] | AsyncGenerator[dict[str, Any], None]:
        """Send a chat completion request.

        When *stream* is ``True`` returns an async generator that yields
        delta dicts.  When ``False`` returns the full response dict.
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            **kwargs,
        }

        if stream:
            return self._stream_chat(body)

        resp = await self._request("POST", "/v1/chat/completions", json=body)
        return resp.json()

    async def _stream_chat(
        self,
        body: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream chat completions, yielding parsed JSON deltas.

        Creates its own ``httpx.AsyncClient`` per call (matching the
        pattern used by :meth:`_request`).  The previous implementation
        referenced ``self._client`` which was never set, causing an
        ``AttributeError`` at runtime.
        """
        url = f"{self.base_url}/v1/chat/completions"
        async with self._make_client() as client:
            async with client.stream(
                "POST",
                url,
                json=body,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        payload = line[6:].strip()
                        if payload == "[DONE]":
                            return
                        yield json.loads(payload)

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def embeddings(
        self,
        model: str,
        input_texts: str | list[str],
    ) -> list[list[float]]:
        """Generate embeddings for *input_texts*.

        Returns a list of embedding vectors.
        """
        body = {
            "model": model,
            "input": input_texts if isinstance(input_texts, list) else [input_texts],
        }
        resp = await self._request("POST", "/v1/embeddings", json=body)
        data = resp.json()
        return [item["embedding"] for item in data["data"]]

    # ------------------------------------------------------------------
    # Image Generation
    # ------------------------------------------------------------------

    async def image_generation(
        self,
        model: str,
        prompt: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate an image from a text prompt.

        Returns the parsed JSON response (may contain a URL or base64 data).
        """
        body = {"model": model, "prompt": prompt, **kwargs}
        resp = await self._request("POST", "/v1/images/generations", json=body)
        data = resp.json()

        # If the response is binary data, wrap it
        content_type = resp.headers.get("content-type", "")
        if "image" in content_type:
            return {"data": [{"b64_json": resp.content.hex()}]}

        return data

    # ------------------------------------------------------------------
    # Text-to-Speech
    # ------------------------------------------------------------------

    async def text_to_speech(
        self,
        model: str,
        input_text: str,
        voice: str = "default",
        **kwargs: Any,
    ) -> bytes:
        """Convert text to speech audio.

        Returns raw binary audio data (usually WAV/MP3).
        """
        body = {"model": model, "input": input_text, "voice": voice, **kwargs}
        resp = await self._request("POST", "/v1/audio/speech", json=body)
        return resp.content

    # ------------------------------------------------------------------
    # Classification (Lemonade Router / ONNX text classifier)
    # ------------------------------------------------------------------

    async def classify(
        self,
        input_text: str | list[str],
        model: str = "routing.router",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """POST /v1/classify — text classification via Lemonade Router/ONNX model.

        Args:
            input_text: Text string or list of text strings to classify.
            model: Model name (e.g. ``routing.router`` or encoder classifier model).

        Returns:
            Parsed JSON classification results.
        """
        body = {
            "model": model,
            "text": input_text,
            **kwargs,
        }
        resp = await self._request("POST", "/v1/classify", json=body)
        return resp.json()

    # ------------------------------------------------------------------
    # System / Health / Models
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Check if the Lemonade server is reachable.

        Returns status dict including models, is_busy, and is_streaming flags
        introduced in Lemonade v11.5.0. On 403 Forbidden, returns status='forbidden'
        with clear diagnostic guidance regarding LEMONADE_ALLOWED_ORIGINS.
        """
        try:
            resp = await self._request("GET", "/v1/health")
            data = {}
            try:
                data = resp.json()
            except Exception:
                pass

            models = data.get("models", []) if isinstance(data, dict) else []
            is_busy = False
            is_streaming = False

            if isinstance(data, dict):
                if "is_busy" in data:
                    is_busy = bool(data["is_busy"])
                elif isinstance(models, list):
                    is_busy = any(bool(m.get("is_busy", False)) for m in models if isinstance(m, dict))

                if "is_streaming" in data:
                    is_streaming = bool(data["is_streaming"])
                elif isinstance(models, list):
                    is_streaming = any(bool(m.get("is_streaming", False)) for m in models if isinstance(m, dict))

            return {
                "status": "ok",
                "code": resp.status_code,
                "models": models,
                "is_busy": is_busy,
                "is_streaming": is_streaming,
                "detail": data,
            }
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                return {
                    "status": "forbidden",
                    "code": 403,
                    "detail": (
                        "403 Forbidden: Origin rejected by Lemonade server. "
                        "Lemonade v11.5+ requires non-loopback origins to be listed in LEMONADE_ALLOWED_ORIGINS."
                    ),
                }
            return {"status": "error", "code": exc.response.status_code, "detail": str(exc)}
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}

    async def get_model_busy_status(self, model_name: Optional[str] = None) -> dict[str, bool]:
        """Inspect busy and streaming state for a given model or overall server.

        Returns dict ``{"is_busy": bool, "is_streaming": bool}``.
        """
        health = await self.health_check()
        if health.get("status") != "ok":
            return {"is_busy": False, "is_streaming": False}

        if model_name:
            models = health.get("models", [])
            if isinstance(models, list):
                for m in models:
                    if isinstance(m, dict) and m.get("name") == model_name:
                        return {
                            "is_busy": bool(m.get("is_busy", False)),
                            "is_streaming": bool(m.get("is_streaming", False)),
                        }
            return {"is_busy": False, "is_streaming": False}

        return {
            "is_busy": bool(health.get("is_busy", False)),
            "is_streaming": bool(health.get("is_streaming", False)),
        }

    async def load_model(self, model_name: str) -> dict[str, Any]:
        """Request the server to load a specific model."""
        body = {"model": model_name}
        resp = await self._request("POST", "/v1/load", json=body)
        return resp.json()

    async def get_models(self) -> list[dict[str, Any]]:
        """List available (loaded) models on the server."""
        resp = await self._request("GET", "/v1/models")
        data = resp.json()
        return data.get("data", data)

    # ------------------------------------------------------------------
    # Stats & Hardware endpoints — gracefully degrade on failure
    # ------------------------------------------------------------------

    async def get_stats(self) -> dict[str, Any]:
        """GET /v1/stats — per-model server stats (ttft, tokens/sec, counts).

        Returns an empty dict if the endpoint is unavailable so callers can
        always inspect the result without exception handling.
        """
        return await self._safe_request_json("GET", "/v1/stats")

    async def system_stats(self) -> dict[str, Any]:
        """GET /v1/system-stats — current resource usage (CPU/GPU/NPU/VRAM/RAM).

        Returns an empty dict if the endpoint is unavailable.
        """
        return await self._safe_request_json("GET", "/v1/system-stats")

    async def system_info(self) -> dict[str, Any]:
        """GET /v1/system-info — static hardware details (OS, CPU, GPU, NPU, RAM).

        Returns an empty dict if the endpoint is unavailable.
        """
        return await self._safe_request_json("GET", "/v1/system-info")

    # ------------------------------------------------------------------
    # Backwards-compatible aliases (older callers)
    # ------------------------------------------------------------------

    async def get_system_stats(self) -> dict[str, Any]:
        """Alias for :meth:`system_stats` (older API name)."""
        return await self.system_stats()

    async def get_system_info(self) -> dict[str, Any]:
        """Alias for :meth:`system_info` (older API name)."""
        return await self.system_info()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        pass

    async def __aenter__(self) -> LemonadeClient:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()
