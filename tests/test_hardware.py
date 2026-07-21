"""Tests for AMD hardware detection and backend recommendation.

These tests run *offline* — they patch the ``LemonadeClient`` methods at
module level so we never touch a real server.  Coverage includes:

* :func:`detect_hardware` against several hardware profiles
  (Ryzen AI full system, Radeon + ROCm only, low-RAM CPU only) and
  against pathological / malformed responses.
* :func:`recommend_backends` for the same profiles plus Apple Silicon.
* :func:`has_amd_hardware` and :func:`is_amd_optimized_target`.
* Pydantic models round-tripping JSON.
* The per-role backend field on :class:`ModelsConfig`.
* Orchestrator pre-flight wired through to the new client methods.
"""

from __future__ import annotations

from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Sample Lemonade responses — shaped like the real /v1/system-info JSON
# ---------------------------------------------------------------------------

RYZEN_AI_FULL_SYSTEM_INFO: dict[str, Any] = {
    "os": {"name": "Linux", "version": "6.5"},
    "cpu": {
        "name": "AMD Ryzen AI MAX+ 395",
        "cores_physical": 16,
        "cores_logical": 32,
        "arch": "x86_64",
    },
    "gpu": [
        {
            "name": "AMD Radeon 8060S Graphics",
            "vendor": "AMD",
            "driver": "6.2.2",
            "vram_gb": 16.0,
            "backend": "rocm",
        }
    ],
    "npu": [
        {
            "name": "AMD Ryzen AI NPU",
            "vendor": "AMD",
            "backend": "ryzenai",
            "available": True,
        }
    ],
    "memory": {"total_gb": 64.0, "available_gb": 48.0},
}

RYZEN_AI_SYSTEM_STATS: dict[str, Any] = {
    "cpu_percent": 12.4,
    "memory_percent": 25.0,
    "memory_gb": 16.0,
    "memory_available_gb": 48.0,
    "gpu_percent": 5.0,
    "gpu_memory_gb": 1.2,
    "npu_percent": 0.0,
}

RAD_RDNA_INFO: dict[str, Any] = {
    "os": "Windows",
    "cpu": {
        "name": "AMD Ryzen 9 7950X",
        "cores_physical": 16,
        "cores_logical": 32,
    },
    "gpus": [
        {
            "name": "Radeon RX 7900 XTX",
            "vendor": "AMD",
            "vram_gb": 24.0,
        }
    ],
    "npu": [],
    "memory": {"total_gb": 32.0, "available_gb": 16.0},
}

LOW_RAM_CPU_INFO: dict[str, Any] = {
    "os": "Linux",
    "cpu": {"name": "Intel Core i5", "cores_physical": 4, "cores_logical": 8},
    "memory": {"total_gb": 8.0, "available_gb": 4.0},
}

APPLE_SILICON_INFO: dict[str, Any] = {
    "os": "Darwin",
    "cpu": {"name": "Apple M3 Pro", "cores_physical": 12, "cores_logical": 12, "arch": "arm64"},
    "gpu": [{"name": "Apple M3 Pro GPU", "vendor": "Apple", "vram_gb": 18.0}],
    "memory": {"total_gb": 36.0, "available_gb": 24.0},
}

NVIDIA_INFO: dict[str, Any] = {
    "os": "Linux",
    "cpu": {"name": "Intel i7", "cores": 16},
    "gpu": [{"name": "NVIDIA GeForce RTX 4090", "vendor": "NVIDIA", "vram_gb": 24.0}],
    "memory": {"total_gb": 64.0, "available_gb": 32.0},
}


# ---------------------------------------------------------------------------
# Fixtures: a mocked LemonadeClient
# ---------------------------------------------------------------------------


class MockClient:
    """Stand-in for :class:`LemonadeClient` used by hardware detection.

    Stores canned responses that the test can override via constructor
    arguments.  Implements the three methods (``system_info``,
    ``system_stats``, ``get_stats``) used by :func:`detect_hardware`.
    """

    def __init__(
        self,
        info: dict[str, Any] | None = None,
        stats: dict[str, Any] | None = None,
        stats_endpoint: dict[str, Any] | None = None,
    ) -> None:
        self._info = info
        self._stats = stats
        self._stats_endpoint = stats_endpoint
        self.info_calls = 0
        self.stats_calls = 0

    async def system_info(self) -> dict[str, Any]:
        self.info_calls += 1
        return dict(self._info) if isinstance(self._info, dict) else {}

    async def system_stats(self) -> dict[str, Any]:
        self.stats_calls += 1
        return dict(self._stats) if isinstance(self._stats, dict) else {}

    async def get_stats(self) -> dict[str, Any]:
        return dict(self._stats_endpoint) if isinstance(self._stats_endpoint, dict) else {}


class FailingClient:
    """Mock that always raises — defensive-code path."""

    async def system_info(self) -> dict[str, Any]:
        raise RuntimeError("endpoint missing")

    async def system_stats(self) -> dict[str, Any]:
        raise RuntimeError("endpoint missing")


# ---------------------------------------------------------------------------
# Tests: detection
# ---------------------------------------------------------------------------


class TestDetectHardware:
    """Detect hardware from mocked Lemonade responses."""

    @pytest.mark.asyncio
    async def test_full_ryzen_ai_system(self) -> None:
        """All fields populated correctly from a Ryzen AI / Radeon / XDNA response."""
        from swarmmind.core.hardware import detect_hardware

        client = MockClient(
            info=RYZEN_AI_FULL_SYSTEM_INFO,
            stats=RYZEN_AI_SYSTEM_STATS,
        )
        hw = await detect_hardware(client)  # type: ignore[arg-type]

        assert hw.os == "Linux"
        assert "Ryzen AI MAX+ 395" in hw.cpu_name
        assert hw.cpu_cores_physical == 16
        assert hw.cpu_cores_logical == 32
        assert hw.cpu_arch == "x86_64"
        assert hw.total_ram_gb == 64.0
        assert hw.available_ram_gb == 48.0

        assert len(hw.gpus) == 1
        gpu = hw.gpus[0]
        assert gpu.vendor == "AMD"
        assert gpu.vram_gb == 16.0
        assert gpu.backend == "rocm"

        assert len(hw.npus) == 1
        npu = hw.npus[0]
        assert npu.vendor == "AMD"
        assert npu.backend == "ryzenai"
        assert npu.available is True

    @pytest.mark.asyncio
    async def test_radeon_only(self) -> None:
        """RDNA-only system parses correctly; NPUs end up empty."""
        from swarmmind.core.hardware import detect_hardware

        client = MockClient(info=RAD_RDNA_INFO)
        hw = await detect_hardware(client)  # type: ignore[arg-type]

        assert hw.os == "Windows"
        assert "Ryzen 9 7950X" in hw.cpu_name
        assert hw.total_ram_gb == 32.0
        assert len(hw.gpus) == 1
        assert hw.gpus[0].vendor == "AMD"
        assert hw.gpus[0].vram_gb == 24.0
        # >4 GB triggers ROCm backend heuristic
        assert hw.gpus[0].backend == "rocm"
        # No NPU data → empty list rather than None / exception
        assert hw.npus == []

    @pytest.mark.asyncio
    async def test_low_ram_cpu_only(self) -> None:
        """CPU-only low-RAM host — no GPUs/NPUs reported; safe defaults."""
        from swarmmind.core.hardware import detect_hardware

        client = MockClient(info=LOW_RAM_CPU_INFO)
        hw = await detect_hardware(client)  # type: ignore[arg-type]

        assert hw.os == "Linux"
        assert "Intel" in hw.cpu_name
        assert hw.total_ram_gb == 8.0
        assert hw.gpus == []
        assert hw.npus == []

    @pytest.mark.asyncio
    async def test_apple_silicon(self) -> None:
        """Apple M-series — vendor auto-detected, Metal backend inferred."""
        from swarmmind.core.hardware import detect_hardware

        client = MockClient(info=APPLE_SILICON_INFO)
        hw = await detect_hardware(client)  # type: ignore[arg-type]

        assert hw.os == "Darwin"
        assert len(hw.gpus) == 1
        assert hw.gpus[0].vendor == "Apple"
        assert hw.gpus[0].backend == "metal"

    @pytest.mark.asyncio
    async def test_nvidia_gpu(self) -> None:
        """NVIDIA GPU → CUDA backend inference."""
        from swarmmind.core.hardware import detect_hardware

        client = MockClient(info=NVIDIA_INFO)
        hw = await detect_hardware(client)  # type: ignore[arg-type]

        assert len(hw.gpus) == 1
        assert hw.gpus[0].vendor == "NVIDIA"
        assert hw.gpus[0].backend == "cuda"

    @pytest.mark.asyncio
    async def test_malformed_json(self) -> None:
        """Malformed / missing keys → empty model, never raises."""
        from swarmmind.core.hardware import detect_hardware

        client = MockClient(
            info={
                "os": "Linux",
                "gpus": "not a list",  # wrong shape
                "memory": {"something_else": 1},
                "npu": None,
            },
        )
        hw = await detect_hardware(client)  # type: ignore[arg-type]
        # Should not raise; gpus/npus are empty
        assert hw.gpus == []
        assert hw.npus == []
        assert hw.os == "Linux"

    @pytest.mark.asyncio
    async def test_empty_endpoints(self) -> None:
        """All endpoints returning empty dicts → placeholder defaults."""
        from swarmmind.core.hardware import detect_hardware

        client = MockClient(info={}, stats={})
        hw = await detect_hardware(client)  # type: ignore[arg-type]

        # OS falls back to platform.system(); CPU/arch to platform.*.
        assert hw.cpu_cores_physical == 0
        assert hw.cpu_cores_logical == 0
        assert hw.total_ram_gb == 0.0
        assert hw.gpus == []
        assert hw.npus == []

    @pytest.mark.asyncio
    async def test_endpoints_raising(self) -> None:
        """If endpoints raise, detection still returns a default model."""
        from swarmmind.core.hardware import detect_hardware

        hw = await detect_hardware(FailingClient())  # type: ignore[arg-type]
        assert hw is not None
        assert hw.gpus == []
        assert hw.npus == []


# ---------------------------------------------------------------------------
# Tests: backend recommendation
# ---------------------------------------------------------------------------


class TestRecommendBackends:
    """Per-role backend preferences for varying hardware profiles."""

    def _hw_from_info(self, info: dict[str, Any]):
        """Detect helper used for the recommendation tests."""
        from swarmmind.core.hardware import _parse_system_info  # noqa: PLC2701

        return _parse_system_info(info)

    def test_ryzen_ai_full_system(self) -> None:
        """Ryzen AI: NPU pointed to embeddings, ROCm preferred elsewhere."""
        from swarmmind.core.hardware import (
            LemonadeBackend,
            recommend_backends,
        )

        hw = self._hw_from_info(RYZEN_AI_FULL_SYSTEM_INFO)
        recs = recommend_backends(hw)

        assert set(recs.keys()) == {"conductor", "worker", "embeddings", "image", "tts"}
        # Conductor/worker/image: ROCm first
        assert recs["conductor"].recommended_backends[0] == LemonadeBackend.ROCM
        assert recs["worker"].recommended_backends[0] == LemonadeBackend.ROCM
        assert recs["image"].recommended_backends[0] == LemonadeBackend.ROCM
        # Embeddings: Ryzen AI NPU first
        assert recs["embeddings"].recommended_backends[0] == LemonadeBackend.RYZEN_AI
        # TTS: CPU preferred (keeps VRAM free)
        assert recs["tts"].recommended_backends[0] == LemonadeBackend.CPU
        # Rationales are non-empty strings
        for role, rec in recs.items():
            assert isinstance(rec.rationale, str)
            assert len(rec.rationale) > 5

    def test_rocm_gpu_only(self) -> None:
        """ROCm-only system: ROCm for compute + image, CPU for TTS."""
        from swarmmind.core.hardware import (
            LemonadeBackend,
            recommend_backends,
        )

        hw = self._hw_from_info(RAD_RDNA_INFO)
        recs = recommend_backends(hw)

        assert recs["conductor"].recommended_backends[0] == LemonadeBackend.ROCM
        assert recs["worker"].recommended_backends[0] == LemonadeBackend.ROCM
        assert recs["image"].recommended_backends[0] == LemonadeBackend.ROCM
        # No NPU → embeddings fall back to ROCm
        assert recs["embeddings"].recommended_backends[0] == LemonadeBackend.ROCM
        # TTS remains CPU-first
        assert recs["tts"].recommended_backends[0] == LemonadeBackend.CPU

    def test_apple_silicon(self) -> None:
        """Apple M-series: AUTO first for conductor (Metal via Lemonade)."""
        from swarmmind.core.hardware import (
            LemonadeBackend,
            recommend_backends,
        )

        hw = self._hw_from_info(APPLE_SILICON_INFO)
        recs = recommend_backends(hw)

        # On macOS, AUTO lets Lemonade pick Metal/anthropic helpers.
        assert recs["conductor"].recommended_backends[0] == LemonadeBackend.AUTO
        assert recs["image"].recommended_backends[0] == LemonadeBackend.AUTO
        assert recs["embeddings"].recommended_backends[0] in {
            LemonadeBackend.AUTO,
            LemonadeBackend.CPU,
        }
        # TTS still CPU-first.
        assert recs["tts"].recommended_backends[0] == LemonadeBackend.CPU

    def test_cpu_only(self) -> None:
        """CPU-only host: every role defaults to CPU."""
        from swarmmind.core.hardware import (
            LemonadeBackend,
            recommend_backends,
        )

        hw = self._hw_from_info(LOW_RAM_CPU_INFO)
        recs = recommend_backends(hw)

        # Even at low RAM, CPU should appear in the recommendations.
        for role in ("conductor", "worker", "embeddings", "image"):
            assert LemonadeBackend.CPU in recs[role].recommended_backends, role
        # TTS is CPU-first by default
        assert recs["tts"].recommended_backends[0] == LemonadeBackend.CPU
        # Explicit low-RAM conductor rationale
        assert "low" in recs["conductor"].rationale.lower() or "ram" in recs["conductor"].rationale.lower()


# ---------------------------------------------------------------------------
# Tests: predicates + helpers
# ---------------------------------------------------------------------------


class TestPredicates:
    """`has_amd_hardware` and `is_amd_optimized_target` snippets."""

    def _hw(self, info: dict[str, Any]):
        from swarmmind.core.hardware import _parse_system_info
        return _parse_system_info(info)

    def test_has_amd_hardware_cpu(self) -> None:
        from swarmmind.core.hardware import has_amd_hardware

        hw = self._hw({"cpu": {"name": "AMD Ryzen 7 7700"}, "memory": {}})
        assert has_amd_hardware(hw) is True

    def test_has_amd_hardware_radeon(self) -> None:
        from swarmmind.core.hardware import has_amd_hardware

        hw = self._hw(RAD_RDNA_INFO)
        assert has_amd_hardware(hw) is True

    def test_has_amd_hardware_intel_only(self) -> None:
        from swarmmind.core.hardware import has_amd_hardware

        hw = self._hw({"cpu": {"name": "Intel Core i7"}, "memory": {}})
        assert has_amd_hardware(hw) is False

    def test_is_amd_optimized_target_with_rocm(self) -> None:
        from swarmmind.core.hardware import is_amd_optimized_target

        hw = self._hw(RAD_RDNA_INFO)
        assert is_amd_optimized_target(hw) is True

    def test_is_amd_optimized_target_with_ryzen_ai(self) -> None:
        from swarmmind.core.hardware import is_amd_optimized_target

        hw = self._hw(RYZEN_AI_FULL_SYSTEM_INFO)
        assert is_amd_optimized_target(hw) is True

    def test_is_amd_optimized_target_intel_only(self) -> None:
        from swarmmind.core.hardware import is_amd_optimized_target

        hw = self._hw(LOW_RAM_CPU_INFO)
        assert is_amd_optimized_target(hw) is False

    def test_describe_hardware_contains_cpu(self) -> None:
        from swarmmind.core.hardware import describe_hardware

        hw = self._hw(RYZEN_AI_FULL_SYSTEM_INFO)
        desc = describe_hardware(hw)
        assert "Ryzen AI MAX+ 395" in desc
        assert "Radeon" in desc  # GPU name appears


class TestValidateBackendChoice:
    """Backend string parsing — alias and normalisation tolerance."""

    def test_known_values(self) -> None:
        from swarmmind.core.hardware import (
            LemonadeBackend,
            validate_backend_choice,
        )

        assert validate_backend_choice("rocm") == LemonadeBackend.ROCM
        assert validate_backend_choice("CPU") == LemonadeBackend.CPU
        assert validate_backend_choice("vulkan") == LemonadeBackend.VULKAN
        assert validate_backend_choice("ryzenai") == LemonadeBackend.RYZEN_AI
        assert validate_backend_choice("fastflowlm") == LemonadeBackend.FASTFLOWLM
        assert validate_backend_choice("auto") == LemonadeBackend.AUTO

    def test_aliases(self) -> None:
        from swarmmind.core.hardware import (
            LemonadeBackend,
            validate_backend_choice,
        )

        assert validate_backend_choice("ryzen-ai") == LemonadeBackend.RYZEN_AI
        assert validate_backend_choice("ryzen_ai") == LemonadeBackend.RYZEN_AI
        assert validate_backend_choice("amd_ai") == LemonadeBackend.RYZEN_AI
        assert validate_backend_choice("FastFlow-LM") == LemonadeBackend.FASTFLOWLM
        assert validate_backend_choice("fastflow_lm") == LemonadeBackend.FASTFLOWLM

    def test_unknown(self) -> None:
        from swarmmind.core.hardware import validate_backend_choice

        assert validate_backend_choice(None) is None
        assert validate_backend_choice("") is None
        assert validate_backend_choice("totally-bogus") is None

    def test_warn_misconfigured(self) -> None:
        """A warning is emitted when ROCm is requested but unavailable."""
        from swarmmind.core.hardware import (
            _parse_system_info,
            warn_misconfigured_backends,
        )

        hw = _parse_system_info(LOW_RAM_CPU_INFO)
        warnings = warn_misconfigured_backends(
            hw,
            {
                "conductor": "rocm",
                "embeddings": "ryzenai",
                "worker": "cpu",
            },
        )
        # Two mismatches: rocm + ryzenai.
        assert len(warnings) == 2
        joined = "\n".join(warnings)
        assert "ROCm" in joined
        assert "RyzenAI" in joined or "NPU" in joined

        # Empty config returns no warnings.
        assert warn_misconfigured_backends(hw, {}) == []


# ---------------------------------------------------------------------------
# Tests: Pydantic models round-trip
# ---------------------------------------------------------------------------


class TestPydanticRoundTrip:
    """The hardware models should be serialisable + de-serialisable."""

    def test_system_hardware_round_trip(self) -> None:
        from swarmmind.core.hardware import (
            GPUInfo,
            NPUInfo,
            SystemHardware,
        )

        hw = SystemHardware(
            os="Linux",
            cpu_name="AMD Ryzen 9",
            cpu_cores_physical=12,
            cpu_cores_logical=24,
            cpu_arch="x86_64",
            gpus=[GPUInfo(name="RX 7900 XT", vendor="AMD", vram_gb=20.0, backend="rocm")],
            npus=[NPUInfo(name="Phoenix NPU", vendor="AMD", backend="ryzenai", available=True)],
            total_ram_gb=32.0,
            available_ram_gb=20.0,
        )

        json_str = hw.model_dump_json()
        reloaded = SystemHardware.model_validate_json(json_str)
        assert reloaded == hw

    def test_backend_recommendation_round_trip(self) -> None:
        from swarmmind.core.hardware import (
            BackendRecommendation,
            LemonadeBackend,
        )

        rec = BackendRecommendation(
            role="conductor",
            recommended_backends=[LemonadeBackend.ROCM, LemonadeBackend.AUTO],
            rationale="ROCm detected",
        )
        data = rec.model_dump()
        assert data["recommended_backends"] == ["rocm", "auto"]

        reloaded = BackendRecommendation.model_validate(data)
        assert reloaded == rec


# ---------------------------------------------------------------------------
# Tests: Config backend field
# ---------------------------------------------------------------------------


class TestConfigBackend:
    """``ModelsConfig.backends`` + ``Config.backend_for`` accessor."""

    def test_default_backends_are_none(self) -> None:
        from swarmmind.config import Config

        cfg = Config()  # type: ignore[call-arg]
        for role in ("conductor", "worker", "embeddings", "image", "tts"):
            assert cfg.backend_for(role) is None

    def test_backend_for_unknown_role(self) -> None:
        from swarmmind.config import Config

        cfg = Config()  # type: ignore[call-arg]
        assert cfg.backend_for("totally-unknown-role") is None

    def test_user_overrides(self) -> None:
        """Passing ``models={"backends": {...}}`` is honored."""
        from swarmmind.config import Config

        cfg = Config(  # type: ignore[call-arg]
            models={
                "backends": {
                    "conductor": "rocm",
                    "embeddings": "ryzenai",
                },
            },
        )
        assert cfg.backend_for("conductor") == "rocm"
        assert cfg.backend_for("embeddings") == "ryzenai"
        # Untouched roles stay None.
        assert cfg.backend_for("worker") is None
        assert cfg.backend_for("tts") is None

    def test_toml_parsing(self) -> None:
        """TOML-shaped config (nested models.backends) parses correctly."""
        from swarmmind.config import Config

        cfg = Config(  # type: ignore[call-arg]
            models={
                "conductor": "my-conductor",
                "backends": {
                    "conductor": "vulkan",
                    "image": "rocm",
                },
            },
        )
        assert cfg.models.conductor == "my-conductor"
        assert cfg.backend_for("conductor") == "vulkan"
        assert cfg.backend_for("image") == "rocm"


# ---------------------------------------------------------------------------
# Tests: Orchestrator wiring
# ---------------------------------------------------------------------------


class TestOrchestratorHardware:
    """The orchestrator pre-flight should detect hardware and emit the event."""

    @pytest.mark.asyncio
    async def test_preflight_emits_hardware_detected(self) -> None:
        """A ``hardware_detected`` event is emitted with the right payload."""
        from swarmmind.config import Config
        from swarmmind.core.orchestrator import Orchestrator
        from swarmmind.lemonade.client import LemonadeClient

        config = Config()  # type: ignore[call-arg]
        client = LemonadeClient("http://localhost:13305")
        orch = Orchestrator(config, client)

        # Patch async methods on the client instance to return canned payloads.
        async def fake_system_info() -> dict[str, Any]:
            return RYZEN_AI_FULL_SYSTEM_INFO

        async def fake_system_stats() -> dict[str, Any]:
            return RYZEN_AI_SYSTEM_STATS

        client.system_info = fake_system_info  # type: ignore[method-assign]
        client.system_stats = fake_system_stats  # type: ignore[method-assign]

        events: list[tuple[str, dict[str, Any]]] = []

        def cb(status: str, detail: dict[str, Any]) -> None:
            events.append((status, detail))

        # Stub out the conductor / workers / synthesis to short-circuit the
        # pipeline.  We only want to test the pre-flight hardware step.
        orch._conductor.decompose_query = _async_return(  # type: ignore[method-assign]
            [{"worker_type": "analysis", "task": "x", "context": "", "reasoning": ""}],
        )
        orch._worker.run = _async_return(  # type: ignore[method-assign]
            {
                "findings": "ok",
                "key_points": [],
                "sources_cited": [],
                "confidence": "medium",
                "gaps": [],
            },
        )
        orch._synthesis.synthesize = _async_return(  # type: ignore[method-assign]
            {"title": "t", "executive_summary": "s", "sections": [], "contradictions": [], "conclusion": "c", "follow_up_questions": []},
        )
        client.health_check = _async_return(  # type: ignore[method-assign]
            {"status": "ok", "code": 200},
        )

        await orch.run(query="test", progress_callback=cb)

        hardware_events = [d for s, d in events if s == "hardware_detected"]
        assert hardware_events, "expected a 'hardware_detected' event"
        payload = hardware_events[-1]
        assert payload["os"] == "Linux"
        assert payload["amd_optimized"] is True
        assert payload["amd_hardware"] is True
        assert payload["total_ram_gb"] == 64.0
        assert isinstance(payload["gpus"], list) and payload["gpus"]
        assert isinstance(payload["npus"], list) and payload["npus"]
        # Conductor recommendation is the first in the priority list.
        assert "conductor" in payload["backends"]

    @pytest.mark.asyncio
    async def test_detect_hardware_caches(self) -> None:
        """Two calls to detect_hardware should hit the client once each."""
        from swarmmind.config import Config
        from swarmmind.core.orchestrator import Orchestrator
        from swarmmind.lemonade.client import LemonadeClient

        config = Config()  # type: ignore[call-arg]
        client = LemonadeClient("http://localhost:13305")
        orch = Orchestrator(config, client)

        mock = MockClient(info=RYZEN_AI_FULL_SYSTEM_INFO)
        client.system_info = mock.system_info  # type: ignore[method-assign]
        client.system_stats = mock.system_stats  # type: ignore[method-assign]

        hw1 = await orch.detect_hardware()
        hw2 = await orch.detect_hardware()
        # Cached → same in-memory object.
        assert hw1 is hw2
        # The mock counted each call once on first invoke; total == 1.
        assert mock.info_calls == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _async_return(value: Any):
    """Return an async function that always returns *value* when awaited."""
    async def _impl(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _impl
