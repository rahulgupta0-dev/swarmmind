"""AMD hardware detection and backend-aware model recommendation.

This module consumes Lemonade's ``/v1/system-info`` and ``/v1/system-stats``
HTTP endpoints (via :class:`LemonadeClient`) and exposes:

* :class:`SystemHardware` — a strongly-typed view of the host hardware
  (CPU, GPU, NPU, RAM, OS).
* :func:`detect_hardware` — async factory that performs the two HTTP calls
  and returns a :class:`SystemHardware` instance.  Defensive: missing
  fields, unexpected JSON shapes, or unrecoverable errors all return
  sensible defaults rather than raising.
* :class:`LemonadeBackend` — enum of the inference backends Lemonade
  can target (ROCm, Vulkan, CPU, Ryzen AI, FastFlowLM, AUTO).
* :class:`BackendRecommendation` and :func:`recommend_backends` —
  produces a per-role backend preference (for conductor, worker,
  embeddings, image and TTS) given the detected hardware.
* :func:`has_amd_hardware` / :func:`is_amd_optimized_target` — quick
  predicates for the UI banner.

The module is intentionally tolerant of differing ``/v1/system-info``
shapes — Lemonade may rename or restructure keys between releases, and
the orchestrator should keep running in any case.
"""

from __future__ import annotations

import logging
import platform
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from swarmmind.lemonade.client import LemonadeClient

logger = logging.getLogger(__name__)


# =============================================================================
# Backend enumeration
# =============================================================================


class LemonadeBackend(str, Enum):
    """Supported Lemonade inference backends.

    Values are stored as lower-case strings so they're safe to embed in
    TOML config files.
    """

    AUTO = "auto"
    ROCM = "rocm"
    VULKAN = "vulkan"
    CPU = "cpu"
    RYZEN_AI = "ryzenai"
    FASTFLOWLM = "fastflowlm"


# =============================================================================
# Hardware models
# =============================================================================


class GPUInfo(BaseModel):
    """A single discrete or integrated GPU detected on the host."""

    name: str = "Unknown"
    vendor: str = "Unknown"  # "AMD" | "NVIDIA" | "Intel" | "Apple" | "Other"
    driver: str = ""
    vram_gb: float = 0.0
    backend: str = "none"  # "rocm" | "vulkan" | "cuda" | "metal" | "none"


class NPUInfo(BaseModel):
    """A single NPU (neural processing unit) detected on the host."""

    name: str = "Unknown"
    vendor: str = "Unknown"
    backend: str = "none"  # "ryzenai" | "fastflowlm" | "none"
    available: bool = False


class SystemHardware(BaseModel):
    """A high-level snapshot of the host's compute capabilities."""

    os: str = "Unknown"
    cpu_name: str = "Unknown"
    cpu_cores_physical: int = 0
    cpu_cores_logical: int = 0
    cpu_arch: str = "unknown"
    gpus: list[GPUInfo] = Field(default_factory=list)
    npus: list[NPUInfo] = Field(default_factory=list)
    total_ram_gb: float = 0.0
    available_ram_gb: float = 0.0


# =============================================================================
# Backend recommendation model
# =============================================================================


class BackendRecommendation(BaseModel):
    """A per-role backend recommendation.

    Attributes:
        role: One of ``"conductor"``, ``"worker"``, ``"embeddings"``,
            ``"image"``, ``"tts"``.
        recommended_backends: Ordered preference list — best first.
        rationale: Plain-text reason for the recommendation, used in the
            UI banner and in warning messages.
    """

    role: str
    recommended_backends: list[LemonadeBackend]
    rationale: str


# =============================================================================
# Low-level parsing helpers (defensive)
# =============================================================================


def _coerce_int(value: Any, default: int = 0) -> int:
    """Return *value* as ``int`` if possible, else *default*."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    """Return *value* as ``float`` if possible, else *default*."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_str(value: Any, default: str = "") -> str:
    """Return *value* as ``str`` if possible, else *default*."""
    if value is None:
        return default
    return str(value)


def _detect_vendor(name: str) -> str:
    """Infer GPU/NPU vendor from a device name string."""
    if not name:
        return "Unknown"
    lowered = name.lower()
    if "amd" in lowered or "radeon" in lowered or "ryzen" in lowered:
        return "AMD"
    if "nvidia" in lowered or "geforce" in lowered or "quadro" in lowered or "tesla" in lowered:
        return "NVIDIA"
    if "intel" in lowered or "arc" in lowered or "iris" in lowered or "uhd" in lowered:
        return "Intel"
    if "apple" in lowered or "m1" in lowered or "m2" in lowered or "m3" in lowered or "m4" in lowered:
        return "Apple"
    return "Other"


def _detect_gpu_backend(name: str, vendor: str, vram_gb: float) -> str:
    """Heuristically pick the most likely GPU backend for a given device.

    * AMD GPUs ≥ 4 GB VRAM → ``rocm``
    * AMD GPUs without enough VRAM → ``vulkan``
    * NVIDIA GPUs → ``cuda``
    * Apple GPUs → ``metal``
    * Intel/Other → ``vulkan``
    """
    lowered = (name or "").lower()
    if vendor == "AMD":
        if any(kw in lowered for kw in ("rx ", "radeon rx", "radeon pro", "instinct")) or vram_gb >= 4.0:
            return "rocm"
        return "vulkan"
    if vendor == "NVIDIA":
        return "cuda"
    if vendor == "Apple":
        return "metal"
    if vendor == "Intel":
        return "vulkan"
    return "none"


def _detect_npu_backend(name: str) -> str:
    """Heuristically detect NPU backend type from a name string."""
    lowered = (name or "").lower()
    if "fastflow" in lowered or "fflm" in lowered:
        return "fastflowlm"
    if "ryzen ai" in lowered or "ryzenai" in lowered or "xDNA" in lowered or "phoenix" in lowered or "strix" in lowered:
        return "ryzenai"
    return "none"


def _local_os_name() -> str:
    """Best-effort OS name from the Python ``platform`` module."""
    try:
        sys_platform = platform.system()
        if sys_platform:
            return sys_platform
    except Exception:
        pass
    return "Unknown"


def _local_cpu_name() -> str:
    """Best-effort CPU name from ``platform.processor``."""
    try:
        name = platform.processor()
        if name:
            return name.strip() or "Unknown"
    except Exception:
        pass
    return "Unknown"


def _local_cpu_arch() -> str:
    """Best-effort CPU architecture (e.g. ``x86_64``)."""
    try:
        return platform.machine() or "unknown"
    except Exception:
        return "unknown"


# =============================================================================
# Top-level parser
# =============================================================================


def _parse_system_info(info: dict[str, Any]) -> SystemHardware:
    """Convert a ``/v1/system-info`` JSON body into :class:`SystemHardware`.

    Defensive — when fields are missing or shaped unexpectedly, the
    corresponding parts of the model are left empty. Local fallbacks
    (from Python's :mod:`platform`) fill in OS/CPU/arch.
    """
    if not isinstance(info, dict):
        info = {}

    # ---- OS ----------------------------------------------------------------
    os_block = info.get("os")
    os_name: str
    if isinstance(os_block, dict):
        os_name = _coerce_str(
            os_block.get("name")
            or os_block.get("system")
            or os_block.get("id"),
            "",
        )
    else:
        os_name = _coerce_str(os_block, "")
    if not os_name:
        os_name = _local_os_name()

    # ---- CPU ---------------------------------------------------------------
    cpu_block = info.get("cpu")
    if not isinstance(cpu_block, dict):
        cpu_block = {}

    cpu_name = (
        _coerce_str(cpu_block.get("name"))
        or _coerce_str(cpu_block.get("brand"))
        or _coerce_str(cpu_block.get("model"))
        or _coerce_str(info.get("cpu_name"))
        or _local_cpu_name()
    )

    cores_physical = (
        _coerce_int(cpu_block.get("cores_physical"))
        or _coerce_int(cpu_block.get("physical_cores"))
        or _coerce_int(cpu_block.get("cores"))
        or _coerce_int(info.get("cpu_cores_physical"))
    )
    cores_logical = (
        _coerce_int(cpu_block.get("cores_logical"))
        or _coerce_int(cpu_block.get("logical_cpus"))
        or _coerce_int(cpu_block.get("threads"))
        or _coerce_int(cpu_block.get("cores"))
        or _coerce_int(info.get("cpu_cores_logical"))
        or cores_physical
    )

    cpu_arch = (
        _coerce_str(cpu_block.get("arch"))
        or _coerce_str(cpu_block.get("architecture"))
        or _coerce_str(info.get("cpu_arch"))
        or _local_cpu_arch()
    )

    # ---- GPUs --------------------------------------------------------------
    gpus: list[GPUInfo] = []
    raw_gpus = info.get("gpu") or info.get("gpus") or info.get("devices")
    gpu_candidates: list[Any]
    if isinstance(raw_gpus, list):
        gpu_candidates = raw_gpus
    elif isinstance(raw_gpus, dict):
        # Some servers wrap devices as {name: {details}}
        gpu_candidates = [
            {"name": k, **(v if isinstance(v, dict) else {})}
            for k, v in raw_gpus.items()
        ]
    else:
        gpu_candidates = []

    for entry in gpu_candidates:
        if not isinstance(entry, dict):
            continue
        # Skip plainly CPU-tagged entries
        kind = _coerce_str(entry.get("type") or entry.get("kind"), "").lower()
        if kind in {"cpu", "processor"}:
            continue

        name = (
            _coerce_str(entry.get("name"))
            or _coerce_str(entry.get("model"))
            or _coerce_str(entry.get("device"))
            or _coerce_str(entry.get("id"))
        )
        vendor_in = (
            _coerce_str(entry.get("vendor"))
            or _coerce_str(entry.get("manufacturer"))
        )
        vendor = vendor_in if vendor_in and vendor_in not in {"Unknown", ""} else _detect_vendor(name)
        driver = _coerce_str(entry.get("driver") or entry.get("driver_version"))
        vram_gb = _coerce_float(
            entry.get("vram_gb")
            or entry.get("memory_gb")
            or entry.get("vram")
            or entry.get("memory_total_gb"),
        )

        backend_in = _coerce_str(entry.get("backend") or entry.get("api"))
        if backend_in in {"rocm", "vulkan", "cuda", "metal", "none"}:
            backend = backend_in
        else:
            backend = _detect_gpu_backend(name, vendor, vram_gb)

        gpus.append(
            GPUInfo(
                name=name or "Unknown",
                vendor=vendor,
                driver=driver,
                vram_gb=vram_gb,
                backend=backend,
            )
        )

    # ---- NPUs --------------------------------------------------------------
    npus: list[NPUInfo] = []
    raw_npus = info.get("npu") or info.get("npus") or info.get("accelerators")
    npu_candidates: list[Any]
    if isinstance(raw_npus, list):
        npu_candidates = raw_npus
    elif isinstance(raw_npus, dict):
        npu_candidates = [
            {"name": k, **(v if isinstance(v, dict) else {})}
            for k, v in raw_npus.items()
        ]
    else:
        npu_candidates = []

    for entry in npu_candidates:
        if not isinstance(entry, dict):
            continue
        name = (
            _coerce_str(entry.get("name"))
            or _coerce_str(entry.get("model"))
            or _coerce_str(entry.get("device"))
        )
        vendor = (
            _coerce_str(entry.get("vendor")) or _detect_vendor(name)
        )
        backend_in = _coerce_str(entry.get("backend"))
        if backend_in in {"ryzenai", "fastflowlm", "none"}:
            backend = backend_in
        else:
            backend = _detect_npu_backend(name)
        available_raw = entry.get("available")
        if isinstance(available_raw, bool):
            available = available_raw
        else:
            available = backend != "none"

        npus.append(
            NPUInfo(
                name=name or "Unknown",
                vendor=vendor or "Unknown",
                backend=backend,
                available=available,
            )
        )

    # ---- Memory ------------------------------------------------------------
    mem_block = info.get("memory")
    if not isinstance(mem_block, dict):
        mem_block = {}
    total_ram_gb = _coerce_float(
        mem_block.get("total_gb")
        or mem_block.get("total")
        or mem_block.get("total_memory_gb")
        or info.get("total_memory_gb")
        or info.get("total_ram_gb"),
    )
    available_ram_gb = _coerce_float(
        mem_block.get("available_gb")
        or mem_block.get("available")
        or mem_block.get("free_gb")
        or info.get("available_memory_gb")
        or info.get("available_ram_gb"),
    )

    return SystemHardware(
        os=os_name,
        cpu_name=cpu_name,
        cpu_cores_physical=cores_physical,
        cpu_cores_logical=cores_logical,
        cpu_arch=cpu_arch,
        gpus=gpus,
        npus=npus,
        total_ram_gb=total_ram_gb,
        available_ram_gb=available_ram_gb,
    )


def _parse_system_stats(stats: dict[str, Any]) -> dict[str, float]:
    """Extract a small dict of useful scalar usage values from ``/v1/system-stats``.

    Defensive — missing keys simply leave their values at 0.0.  The
    result is used by :class:`SystemHardware.available_ram_gb` so we can
    refine the static RAM totals with live free numbers.
    """
    out: dict[str, float] = {}
    if not isinstance(stats, dict):
        return out

    for key in (
        "cpu_percent", "memory_percent", "memory_gb", "memory_available_gb",
        "available_memory_gb", "free_memory_gb",
        "gpu_percent", "gpu_memory_gb", "npu_percent",
        "vram_used_gb", "vram_total_gb",
    ):
        val = stats.get(key)
        if val is not None:
            try:
                out[key] = float(val)
            except (TypeError, ValueError):
                continue
    return out


def _merge_ram_into(hw: SystemHardware, stats: dict[str, float]) -> None:
    """Augment :class:`SystemHardware` RAM fields with live values when
    they weren't already populated from ``/v1/system-info``.
    """
    if hw.available_ram_gb <= 0:
        for key in ("memory_available_gb", "available_memory_gb", "free_memory_gb"):
            if key in stats:
                hw.available_ram_gb = stats[key]
                break
    if hw.total_ram_gb <= 0:
        for key in ("memory_gb", "memory_total_gb"):
            # Some servers don't send total RAM here, only used/available.
            if key in stats and hw.total_ram_gb == 0:
                # memory_gb may be *used*, so don't overwrite — only set if
                # we have absolutely no value.
                pass


# =============================================================================
# Public async factory
# =============================================================================


async def detect_hardware(client: LemonadeClient) -> SystemHardware:
    """Detect the host hardware via Lemonade's system endpoints.

    Issues ``GET /v1/system-info`` (and optionally ``/v1/system-stats``)
    against the given *client* and returns a populated
    :class:`SystemHardware`.  If the endpoints are missing, the network
    is unreachable, or the JSON is malformed, a populated-but-maybe-empty
    model is returned and nothing is raised.

    Always safe to call — it's the public entry point used by the
    orchestrator pre-flight and the Streamlit banner.
    """
    # 1. System info — primary source of truth for static hardware
    info_payload: dict[str, Any] = {}
    try:
        info_payload = await client.system_info()
    except Exception as exc:
        logger.warning("detect_hardware: system_info failed: %s", exc)
        info_payload = {}

    if not isinstance(info_payload, dict):
        info_payload = {}

    hardware = _parse_system_info(info_payload)

    # 2. System stats — opportunistic refresh of RAM availability
    try:
        stats_payload = await client.system_stats()
        if isinstance(stats_payload, dict):
            stats = _parse_system_stats(stats_payload)
            _merge_ram_into(hardware, stats)
    except Exception as exc:
        logger.debug("detect_hardware: system_stats not available: %s", exc)

    return hardware


# =============================================================================
# Backend recommendation
# =============================================================================


def _has_rocm_gpu(hw: SystemHardware) -> bool:
    return any(g.backend == "rocm" for g in hw.gpus)


def _has_vulkan_gpu(hw: SystemHardware) -> bool:
    """True if any detected GPU can fall back to a Vulkan path."""
    if not hw.gpus:
        return False
    return any(g.vendor in {"AMD", "Intel", "NVIDIA"} for g in hw.gpus) or any(
        g.backend == "vulkan" for g in hw.gpus
    )


def _has_apple_gpu(hw: SystemHardware) -> bool:
    return any(g.vendor == "Apple" for g in hw.gpus)


def _has_nvidia_gpu(hw: SystemHardware) -> bool:
    return any(g.vendor == "NVIDIA" for g in hw.gpus)


def _has_ryzen_ai_npu(hw: SystemHardware) -> bool:
    return any(n.backend == "ryzenai" and n.available for n in hw.npus)


def _has_fastflowlm_npu(hw: SystemHardware) -> bool:
    return any(n.backend == "fastflowlm" and n.available for n in hw.npus)


def _is_low_ram(hw: SystemHardware) -> bool:
    return hw.total_ram_gb > 0 and hw.total_ram_gb < 16.0


def recommend_backends(hardware: SystemHardware) -> dict[str, BackendRecommendation]:
    """Return backend recommendations for each SwarmMind role.

    Heuristic summary:

    * **RGB NPU (Ryzen AI / FastFlowLM)** — recommended for *embeddings*
      because it sips power and embeddings are a steady-state workload.
    * **AMD Radeon + ROCm** — recommended for *conductor* and *analysis*
      work (large prompts, high VRAM). Also fine for image generation.
    * **AMD Radeon / other Vulkan GPU** — recommended for *synthesis*
      (long multi-token generation).
    * **Any GPU (Apple/Intel/NVIDIA)** — generic Vulkan/CUDA/Metal paths.
    * **No GPU + low RAM** — restrict to CPU and warn about model size.
    """
    rocm = _has_rocm_gpu(hardware)
    vulkan = _has_vulkan_gpu(hardware)
    apple = _has_apple_gpu(hardware)
    nvidia = _has_nvidia_gpu(hardware)
    ryzen_ai = _has_ryzen_ai_npu(hardware)
    fastflowlm = _has_fastflowlm_npu(hardware)
    low_ram = _is_low_ram(hardware)

    result: dict[str, BackendRecommendation] = {}

    # ---- Conductor ----------------------------------------------------------
    if rocm:
        result["conductor"] = BackendRecommendation(
            role="conductor",
            recommended_backends=[
                LemonadeBackend.ROCM,
                LemonadeBackend.VULKAN,
                LemonadeBackend.AUTO,
            ],
            rationale=(
                "AMD Radeon GPU detected with ROCm — recommend ROCm for the "
                "conductor (large-context reasoning benefits from high VRAM)."
            ),
        )
    elif apple:
        result["conductor"] = BackendRecommendation(
            role="conductor",
            recommended_backends=[
                LemonadeBackend.AUTO,
                LemonadeBackend.VULKAN,
                LemonadeBackend.CPU,
            ],
            rationale=(
                "Apple Silicon detected — AUTO/Metal paths usually give the "
                "best conductor throughput on macOS."
            ),
        )
    elif nvidia:
        result["conductor"] = BackendRecommendation(
            role="conductor",
            recommended_backends=[
                LemonadeBackend.AUTO,
                LemonadeBackend.VULKAN,
                LemonadeBackend.CPU,
            ],
            rationale=(
                "NVIDIA GPU detected — AUTO/CUDA via Onyx path; check "
                "Lemonade for current CUDA support status."
            ),
        )
    elif low_ram:
        result["conductor"] = BackendRecommendation(
            role="conductor",
            recommended_backends=[LemonadeBackend.CPU],
            rationale=(
                "Low RAM (<16 GB) and no GPU detected — restrict the "
                "conductor to CPU and use a smaller model (8 B or lower)."
            ),
        )
    elif vulkan:
        result["conductor"] = BackendRecommendation(
            role="conductor",
            recommended_backends=[
                LemonadeBackend.VULKAN,
                LemonadeBackend.CPU,
                LemonadeBackend.AUTO,
            ],
            rationale="Vulkan-capable GPU detected — fallback before CPU.",
        )
    else:
        result["conductor"] = BackendRecommendation(
            role="conductor",
            recommended_backends=[LemonadeBackend.CPU, LemonadeBackend.AUTO],
            rationale="No GPU detected — CPU only; consider smaller models.",
        )

    # ---- Worker -------------------------------------------------------------
    if rocm:
        result["worker"] = BackendRecommendation(
            role="worker",
            recommended_backends=[
                LemonadeBackend.ROCM,
                LemonadeBackend.VULKAN,
                LemonadeBackend.AUTO,
            ],
            rationale="ROCm path is fast and efficient for parallel workers.",
        )
    elif vulkan:
        result["worker"] = BackendRecommendation(
            role="worker",
            recommended_backends=[
                LemonadeBackend.VULKAN,
                LemonadeBackend.CPU,
                LemonadeBackend.AUTO,
            ],
            rationale="Vulkan fallback works well for parallel worker tasks.",
        )
    else:
        result["worker"] = BackendRecommendation(
            role="worker",
            recommended_backends=[LemonadeBackend.CPU, LemonadeBackend.AUTO],
            rationale="No GPU detected — workers will run on CPU.",
        )

    # ---- Embeddings (NPU-should-be-the-default) -----------------------------
    if ryzen_ai:
        result["embeddings"] = BackendRecommendation(
            role="embeddings",
            recommended_backends=[
                LemonadeBackend.RYZEN_AI,
                LemonadeBackend.CPU,
                LemonadeBackend.AUTO,
            ],
            rationale=(
                "Ryzen AI NPU detected — recommended for low-power, "
                "steady-state RAG embeddings."
            ),
        )
    elif fastflowlm:
        result["embeddings"] = BackendRecommendation(
            role="embeddings",
            recommended_backends=[
                LemonadeBackend.FASTFLOWLM,
                LemonadeBackend.CPU,
                LemonadeBackend.AUTO,
            ],
            rationale=(
                "FastFlowLM NPU detected — use it for embeddings to free up "
                "the GPU for generation."
            ),
        )
    elif rocm:
        result["embeddings"] = BackendRecommendation(
            role="embeddings",
            recommended_backends=[
                LemonadeBackend.ROCM,
                LemonadeBackend.VULKAN,
                LemonadeBackend.AUTO,
            ],
            rationale="ROCm path gives fast, batched embeddings on AMD GPUs.",
        )
    elif apple:
        result["embeddings"] = BackendRecommendation(
            role="embeddings",
            recommended_backends=[
                LemonadeBackend.AUTO,
                LemonadeBackend.CPU,
            ],
            rationale="Apple Silicon can accelerate embeddings via ANE/Metal.",
        )
    else:
        result["embeddings"] = BackendRecommendation(
            role="embeddings",
            recommended_backends=[LemonadeBackend.CPU, LemonadeBackend.AUTO],
            rationale="No GPU/NPU detected — embeddings fall back to CPU.",
        )

    # ---- Image generation (Flux) -------------------------------------------
    if rocm:
        result["image"] = BackendRecommendation(
            role="image",
            recommended_backends=[
                LemonadeBackend.ROCM,
                LemonadeBackend.VULKAN,
                LemonadeBackend.AUTO,
            ],
            rationale="Flux image generation benefits from ROCm VRAM headroom.",
        )
    elif apple:
        result["image"] = BackendRecommendation(
            role="image",
            recommended_backends=[
                LemonadeBackend.AUTO,
                LemonadeBackend.CPU,
            ],
            rationale="Apple Metal can run Flux with reduced batch sizes.",
        )
    elif vulkan:
        result["image"] = BackendRecommendation(
            role="image",
            recommended_backends=[
                LemonadeBackend.VULKAN,
                LemonadeBackend.CPU,
                LemonadeBackend.AUTO,
            ],
            rationale="Vulkan fallback for image gen when no ROCm stack.",
        )
    else:
        result["image"] = BackendRecommendation(
            role="image",
            recommended_backends=[LemonadeBackend.CPU, LemonadeBackend.AUTO],
            rationale="Image generation will be slow on CPU — consider GPU.",
        )

    # ---- TTS (Kokoro) -------------------------------------------------------
    # Kokoro is tiny — any backend works. CPU keeps the GPU free.
    result["tts"] = BackendRecommendation(
        role="tts",
        recommended_backends=[LemonadeBackend.CPU, LemonadeBackend.AUTO],
        rationale=(
            "Kokoro narration is tiny — CPU is enough and leaves VRAM free "
            "for concurrent generation."
        ),
    )

    return result


# =============================================================================
# Quick predicates
# =============================================================================


def has_amd_hardware(hardware: SystemHardware) -> bool:
    """Return ``True`` if any AMD-branded device is present.

    Detects AMD *CPU*, AMD *Radeon GPU*, and AMD *Ryzen AI / XDNA NPU*.
    """
    if "amd" in hardware.cpu_name.lower():
        return True
    if "ryzen" in hardware.cpu_name.lower() and "amd" not in hardware.cpu_name.lower():
        # "AMD Ryzen" usually appears as one string; be defensive anyway.
        return True
    if any(g.vendor == "AMD" for g in hardware.gpus):
        return True
    if any(n.vendor == "AMD" for n in hardware.npus):
        return True
    return False


def is_amd_optimized_target(hardware: SystemHardware) -> bool:
    """Return ``True`` if the host is on the highest-value AMD stack.

    Specifically: an AMD Radeon GPU backed by ROCm, or an available
    Ryzen AI / FastFlowLM NPU. These are the configurations that
    showcase AMD hardware the most.
    """
    if any(g.backend == "rocm" for g in hardware.gpus):
        return True
    if any(n.available and n.backend in {"ryzenai", "fastflowlm"} for n in hardware.npus):
        return True
    return False


def describe_hardware(hardware: SystemHardware) -> str:
    """Return a short, human-readable description of the hardware.

    Used as the body text in the AMD optimization banner.
    """
    parts: list[str] = []
    if hardware.cpu_name and hardware.cpu_name != "Unknown":
        parts.append(hardware.cpu_name)
    if hardware.gpus:
        gpu_summaries = [
            f"{g.name} ({g.vendor}, {g.vram_gb:.1f} GB, {g.backend})"
            for g in hardware.gpus
        ]
        parts.append("GPU: " + "; ".join(gpu_summaries))
    if hardware.npus:
        npu_summaries = [
            f"{n.name} ({n.vendor}, {n.backend})"
            for n in hardware.npus
        ]
        parts.append("NPU: " + "; ".join(npu_summaries))
    if hardware.total_ram_gb > 0:
        parts.append(f"RAM: {hardware.total_ram_gb:.1f} GB")
    if hardware.os and hardware.os != "Unknown":
        parts.append(f"OS: {hardware.os}")
    return " | ".join(parts) if parts else "Unknown"


# =============================================================================
# Config compatibility — validate backend overrides
# =============================================================================


_VALID_BACKEND_LOOKUP: dict[str, LemonadeBackend] = {b.value: b for b in LemonadeBackend}


def validate_backend_choice(choice: str | None) -> LemonadeBackend | None:
    """Parse a user-provided backend string (e.g. ``"rocm"``) to an enum.

    Returns ``None`` for ``None`` / empty / unrecognised input so callers
    can fall back to AUTO without raising.
    """
    if not choice:
        return None
    if not isinstance(choice, str):
        return None
    key = choice.strip().lower()
    # Common aliases
    alias = {
        "ryzen-ai": "ryzenai",
        "ryzen_ai": "ryzenai",
        "amd_ai": "ryzenai",
        "fastflow-lm": "fastflowlm",
        "fastflow_lm": "fastflowlm",
        "fastflowlm": "fastflowlm",
    }.get(key, key)
    if alias in _VALID_BACKEND_LOOKUP:
        return _VALID_BACKEND_LOOKUP[alias]
    # Be permissive with minor differences (e.g. "ROCm" -> "rocm")
    normalised = re.sub(r"[^a-z0-9]", "", alias)
    for backend in LemonadeBackend:
        if normalised == re.sub(r"[^a-z0-9]", "", backend.value):
            return backend
    return None


def warn_misconfigured_backends(
    hardware: SystemHardware,
    config_backends: dict[str, str | None],
) -> list[str]:
    """Return a list of human-readable warnings for backend mismatches.

    Each warning is a single sentence suitable for showing in the Streamlit
    UI or in the orchestrator's pre-flight log.  Empty list means the
    user's backend choices are all valid for the detected hardware.
    """
    warnings: list[str] = []
    if not config_backends:
        return warnings

    used_backends = {
        b.value
        for role, choice in config_backends.items()
        if choice and (b := validate_backend_choice(choice)) is not None
    }

    if "rocm" in used_backends and not _has_rocm_gpu(hardware):
        warnings.append(
            "Configured ROCm backend but no AMD Radeon GPU with ROCm "
            "support was detected — Lemonade will likely fall back to CPU."
        )
    if "ryzenai" in used_backends and not _has_ryzen_ai_npu(hardware):
        warnings.append(
            "Configured RyzenAI backend but no Ryzen AI NPU was detected — "
            "the request will probably fail or be downgraded to CPU."
        )
    if "fastflowlm" in used_backends and not _has_fastflowlm_npu(hardware):
        warnings.append(
            "Configured FastFlowLM backend but no FastFlowLM NPU was "
            "detected — install the FastFlowLM runtime first."
        )

    return warnings
