#!/usr/bin/env python3
"""Mock Lemonade server for end-to-end testing of SwarmMind."""

from __future__ import annotations

import json
import logging
import os
import struct
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("mock-lemonade")

PORT = int(os.environ.get("MOCK_PORT", "13305"))

_MOCK_EMBEDDING = [0.1, 0.2, 0.3, 0.4]


def _make_wav() -> bytes:
    """Generate a minimal valid WAV file (silence, 1 sec, 8kHz, mono, 8-bit)."""
    sample_rate = 8000
    duration = 1
    num_samples = sample_rate * duration
    data = b"\x80" * num_samples  # silence in 8-bit
    data_size = len(data)
    chunk_size = 36 + data_size
    fmt = struct.pack("<HHIIHH", 1, 1, sample_rate, sample_rate, 1, 8)
    wav = b"RIFF"
    wav += struct.pack("<I", chunk_size)
    wav += b"WAVE"
    wav += b"fmt "
    wav += struct.pack("<I", 16)
    wav += fmt
    wav += b"data"
    wav += struct.pack("<I", data_size)
    wav += data
    return wav


class MockHandler(BaseHTTPRequestHandler):
    """Handle mock Lemonade API requests."""

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_binary(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw)

    def log_request(self, code: Any = ..., size: Any = ...) -> None:
        log.info("[%s] %s %s", code, self.command, self.path)

    # ---- Route handlers ----

    def _route(self) -> None:
        path = self.path.split("?")[0]
        method = self.command

        # CORS preflight
        if method == "OPTIONS":
            self._send_json(200, {})
            return

        if method == "GET" and path == "/v1/health":
            return self._handle_health()
        if method == "GET" and path == "/v1/models":
            return self._handle_list_models()
        if method == "POST" and path == "/v1/chat/completions":
            return self._handle_chat()
        if method == "POST" and path == "/v1/embeddings":
            return self._handle_embeddings()
        if method == "POST" and path == "/v1/load":
            return self._handle_load()
        if method == "POST" and path == "/v1/images/generations":
            return self._handle_image()
        if method == "POST" and path == "/v1/audio/speech":
            return self._handle_tts()
        if method == "GET" and path == "/v1/stats":
            return self._handle_stats()
        if method == "GET" and path == "/v1/system-stats":
            return self._handle_system_stats()
        if method == "GET" and path == "/v1/system-info":
            return self._handle_system_info()

        self._send_json(404, {"error": "not_found", "path": path})

    def do_GET(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def do_OPTIONS(self) -> None:
        self._route()

    # ---- Endpoint implementations ----

    def _handle_health(self) -> None:
        self._send_json(200, {"status": "ok"})

    def _handle_list_models(self) -> None:
        self._send_json(200, {
            "data": [
                {"id": "Qwen3.6-35B-A3B-GGUF", "object": "model", "created": int(time.time()), "owned_by": "lemonade"},
                {"id": "Gemma-4-12B-it", "object": "model", "created": int(time.time()), "owned_by": "lemonade"},
                {"id": "nomic-embed-text-v1-GGUF", "object": "model", "created": int(time.time()), "owned_by": "lemonade"},
                {"id": "Flux-2-Klein-4B", "object": "model", "created": int(time.time()), "owned_by": "lemonade"},
                {"id": "kokoro-v1", "object": "model", "created": int(time.time()), "owned_by": "lemonade"},
            ]
        })

    def _handle_chat(self) -> None:
        body = self._read_body()
        stream = body.get("stream", False)
        model = body.get("model", "unknown")
        messages = body.get("messages", [])
        user_msg = ""
        for m in messages:
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break

        response_text = (
            f"This is a mock research response to: '{user_msg[:100]}'. "
            f"Based on my analysis of the available sources [1], there are several "
            f"key factors to consider. First, the primary approach involves understanding "
            f"the core trade-offs [2]. Second, the implementation details matter "
            f"significantly for real-world performance. In conclusion, both approaches "
            f"have their merits depending on the specific use case."
        )

        if stream:
            # SSE streaming
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            words = response_text.split()
            for i, word in enumerate(words):
                chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": word + " "}, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()

            done = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            self.wfile.write(f"data: {json.dumps(done)}\n\n".encode())
            self.wfile.write("data: [DONE]\n\n".encode())
            self.wfile.flush()
        else:
            self._send_json(200, {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": response_text},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 50, "completion_tokens": 80, "total_tokens": 130},
            })

    def _handle_embeddings(self) -> None:
        body = self._read_body()
        model = body.get("model", "unknown")
        inputs = body.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]

        data = [
            {"embedding": _MOCK_EMBEDDING, "index": i}
            for i in range(len(inputs))
        ]
        self._send_json(200, {"data": data, "model": model, "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)}})

    def _handle_load(self) -> None:
        body = self._read_body()
        model = body.get("model", "unknown")
        self._send_json(200, {"status": "ok", "model": model})

    def _handle_image(self) -> None:
        # 1x1 transparent PNG
        pixel = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        self._send_json(200, {"data": [{"b64_json": pixel, "revised_prompt": "Mock diagram"}]})

    def _handle_tts(self) -> None:
        wav = _make_wav()
        self._send_binary(200, wav, "audio/wav")

    def _handle_stats(self) -> None:
        self._send_json(200, {"requests_total": 42, "tokens_total": 1337, "model_stats": {}})

    def _handle_system_stats(self) -> None:
        self._send_json(200, {
            "cpu_percent": 25.5, "memory_percent": 45.2, "memory_gb": 15.0,
            "gpu_percent": 0, "gpu_memory_gb": 0,
        })

    def _handle_system_info(self) -> None:
        self._send_json(200, {
            "os": "Linux", "python_version": "3.11",
            "devices": [{"name": "CPU", "type": "cpu"}],
            "lemonade_version": "9.1.4",
        })


def main() -> None:
    server = HTTPServer(("0.0.0.0", PORT), MockHandler)
    print(f"\n🧪 Mock Lemonade Server running on http://localhost:{PORT}")
    print(f"   Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down mock server...")
        server.shutdown()


if __name__ == "__main__":
    main()
