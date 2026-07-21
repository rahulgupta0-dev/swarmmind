"""Reusable Streamlit components for AMD runtime metrics display.

All three public components (:func:`render_hardware_card`,
:func:`render_stats_dashboard`, :func:`render_backend_recommendations_card`)
are pure-presentation helpers that take data (already-fetched or a client)
and render into the current Streamlit context.

They are designed to fit narrow panels (~280px+) and gracefully handle
empty/None/missing data without crashing.
"""

from __future__ import annotations

import time
from typing import Any

from swarmmind.core.hardware import (
    BackendRecommendation,
    GPUInfo,
    NPUInfo,
    SystemHardware,
    LemonadeBackend,
)
from swarmmind.core.metrics import MetricsSnapshot, fetch_snapshot
from swarmmind.lemonade.client import LemonadeClient
from swarmmind.ui.components.icons import icon_html

# ---------------------------------------------------------------------------
# render_hardware_card
# ---------------------------------------------------------------------------


def render_hardware_card(hw: SystemHardware) -> None:
    """Display a compact hardware summary card with Phosphor icons.

    Shows CPU (name + cores), RAM (total + available), each GPU with
    vendor/VRAM/backend, and each NPU with status/backend.  Missing or
    zero-value fields are silently skipped.

    Args:
        hw: Detected :class:`SystemHardware` instance.
    """
    import streamlit as st

    hw_icon = icon_html("desktop", "regular", "1.2em")
    st.markdown(f"**{hw_icon} Hardware**", unsafe_allow_html=True)

    # --- CPU ----------------------------------------------------------------
    cpu_icon = icon_html("cpu", "regular", "1.2em")
    if hw.cpu_name and hw.cpu_name != "Unknown":
        cores = ""
        if hw.cpu_cores_logical:
            cores = f" ({hw.cpu_cores_physical}p/{hw.cpu_cores_logical}l)"
        st.markdown(
            f"{cpu_icon} **CPU:** {hw.cpu_name}{cores}",
            unsafe_allow_html=True,
        )
    elif hw.cpu_cores_logical:
        st.markdown(
            f"{cpu_icon} **CPU:** {hw.cpu_cores_logical} logical cores",
            unsafe_allow_html=True,
        )

    # --- RAM ----------------------------------------------------------------
    if hw.total_ram_gb > 0:
        ram_icon = icon_html("hard-drives", "regular", "1.2em")
        avail = hw.available_ram_gb if hw.available_ram_gb > 0 else hw.total_ram_gb
        st.markdown(
            f"{ram_icon} **RAM:** {hw.total_ram_gb:.1f} GiB total"
            f"  \n&nbsp;&nbsp;&nbsp;Available: {avail:.1f} GiB",
            unsafe_allow_html=True,
        )

    # --- GPUs ---------------------------------------------------------------
    for gpu in hw.gpus:
        _render_gpu_row(gpu)

    # --- NPUs ---------------------------------------------------------------
    for npu in hw.npus:
        _render_npu_row(npu)


def _render_gpu_row(gpu: GPUInfo) -> None:
    """Render a single GPU info row with an appropriate backend icon."""
    import streamlit as st

    icon_map: dict[str, str] = {
        "rocm": "lightning",
        "vulkan": "stack",
        "cuda": "cube",
        "metal": "apple-logo",
        "none": "warning-circle",
    }
    icon_name = icon_map.get(gpu.backend, "cpu")
    icon = icon_html(icon_name, "regular", "1.2em")

    backend_label = gpu.backend.upper() if gpu.backend != "none" else ""
    vram = f", {gpu.vram_gb:.1f} GiB VRAM" if gpu.vram_gb > 0 else ""
    label = gpu.name if gpu.name != "Unknown" else "GPU"

    lines = [f"{icon} **GPU:** {label}"]
    lines.append(f"&nbsp;&nbsp;&nbsp;{gpu.vendor}{vram}")
    if backend_label:
        lines.append(f"&nbsp;&nbsp;&nbsp;{icon_html('lightning', 'fill', '1.2em')} {backend_label}")

    st.markdown("  \n".join(lines), unsafe_allow_html=True)


def _render_npu_row(npu: NPUInfo) -> None:
    """Render a single NPU info row with status indicator."""
    import streamlit as st

    icon = icon_html("microchip", "regular", "1.2em")
    available_icon = icon_html("check-circle", "fill", "1.2em")
    inactive_icon = icon_html("circle", "regular", "1.2em")
    status_icon = available_icon if npu.available else inactive_icon
    status = f"{status_icon} {'Available' if npu.available else 'Inactive'}"
    backend_label = npu.backend.upper() if npu.backend != "none" else ""
    label = npu.name if npu.name != "Unknown" else "NPU"

    lines = [f"{icon} **NPU:** {label}"]
    lines.append(f"&nbsp;&nbsp;&nbsp;{status}")
    if backend_label:
        lines.append(f"&nbsp;&nbsp;&nbsp;{backend_label}")

    st.markdown("  \n".join(lines), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# render_stats_dashboard
# ---------------------------------------------------------------------------


def render_stats_dashboard(
    client: LemonadeClient,
    refresh_seconds: float = 2.0,
) -> None:
    """Live-runtime-metrics dashboard with an optional auto-refresh toggle.

    Fetches ``/v1/stats`` + ``/v1/system-stats`` (via
    :func:`~swarmmind.core.metrics.fetch_snapshot`) and shows:

    * Per-model tokens/second, prompt/completion token counts
    * VRAM / RAM utilisation (with progress bars)
    * NPU utilisation when an NPU is present
    * Timestamp of the last fetch

    Use the toggle button to enable auto-refresh: a 2-second ``time.sleep``
    + ``st.rerun()`` loop.  When auto-refresh is off a single snapshot is
    shown (static).

    Args:
        client: Initialised :class:`LemonadeClient`.
        refresh_seconds: Polling interval (seconds) when auto-refresh is
            active.  Default ``2.0``.
    """
    import streamlit as st

    chart_icon = icon_html("chart-bar", "regular", "1.2em")
    st.markdown(f"**{chart_icon} AMD Runtime Metrics**", unsafe_allow_html=True)

    # Auto-refresh toggle via session state
    toggle_key = "_amd_metrics_auto"
    if toggle_key not in st.session_state:
        st.session_state[toggle_key] = False

    if st.button("Pause" if st.session_state[toggle_key] else "Auto-Refresh", key="_metrics_auto_btn", use_container_width=True):
        st.session_state[toggle_key] = not st.session_state[toggle_key]
        st.rerun()

    run_every = refresh_seconds if st.session_state[toggle_key] else None

    @st.fragment(run_every=run_every)
    def _auto_refresh_metrics():
        placeholder = st.empty()
        _fetch_and_render_metrics(placeholder, client)

    _auto_refresh_metrics()


def _fetch_and_render_metrics(
    placeholder: Any,
    client: LemonadeClient,
) -> None:
    """Fetch a metrics snapshot and render it into *placeholder*."""
    import streamlit as st

    from swarmmind.core.async_utils import run_async  # noqa: PLC0415

    try:
        snapshot = run_async(fetch_snapshot(client))
    except Exception:
        with placeholder.container():
            warning_icon = icon_html("warning-octagon", "fill", "1.2em")
            st.markdown(
                f"{warning_icon} Could not fetch metrics. Is Lemonade running?",
                unsafe_allow_html=True,
            )
        return

    with placeholder.container():
        _render_metrics_content(st, snapshot)


def _render_metrics_content(st: Any, snap: MetricsSnapshot) -> None:
    """Render the metrics snapshot content into Streamlit."""
    stats = snap.stats or {}
    sys_stats = snap.system_stats or {}

    # ---- Per-model stats ---------------------------------------------------
    models_data = _extract_model_stats(stats)
    if models_data:
        for model_name, mdata in models_data.items():
            tps = mdata.get("tokens_per_second", 0.0)
            if tps:
                st.markdown(
                    f"**{model_name}** — {icon_html('lightning', 'fill', '1.2em')} {tps:.1f} tok/s",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(f"**{model_name}**")

            details: list[str] = []
            if mdata.get("prompt_tokens"):
                details.append(f"{icon_html('note-pencil', 'regular', '0.9em')} {mdata['prompt_tokens']} prompt")
            if mdata.get("completion_tokens"):
                details.append(f"{icon_html('check-circle', 'fill', '0.9em')} {mdata['completion_tokens']} completed")
            if mdata.get("ttft_ms"):
                details.append(f"{icon_html('clock', 'regular', '0.9em')} TTFT: {mdata['ttft_ms']:.0f} ms")
            if details:
                st.markdown(
                    f'<span style="font-size:0.85rem; color:#64748b;">{" · ".join(details)}</span>',
                    unsafe_allow_html=True,
                )
    else:
        # Try to show raw keys as a fallback
        if stats:
            keys = list(stats.keys())[:6]
            st.caption(f"Stats: {', '.join(keys)}")
        else:
            st.caption("No per-model stats available.")

    st.divider()

    # ---- VRAM utilisation --------------------------------------------------
    vram_used = _safe_float(sys_stats, "vram_used_gb", "vram_used", "gpu_memory_gb")
    vram_total = _safe_float(sys_stats, "vram_total_gb", "vram_total", "vram_total_gb")
    if vram_total > 0 and vram_used > 0:
        pct = min((vram_used / vram_total) * 100, 100.0)
        c1, c2 = st.columns(2)
        c1.metric("VRAM Used", f"{vram_used:.1f} GiB")
        c2.metric("VRAM Total", f"{vram_total:.1f} GiB")
        st.progress(pct / 100.0)
    elif vram_used > 0:
        st.metric("VRAM", f"{vram_used:.1f} GiB")

    # ---- RAM utilisation ---------------------------------------------------
    mem_pct = _safe_float(sys_stats, "memory_percent", "mem_percent")
    mem_used = _safe_float(sys_stats, "memory_gb", "mem_used_gb", "memory_used_gb", "available_memory_gb")  # noqa: E501
    if mem_pct > 0:
        st.metric("RAM", f"{mem_pct:.0f}%")
        st.progress(min(mem_pct / 100.0, 1.0))
    elif mem_used > 0:
        st.metric("RAM Used", f"{mem_used:.1f} GiB")

    # ---- NPU utilisation ---------------------------------------------------
    npu_pct = _safe_float(sys_stats, "npu_percent", "npu_utilization")
    if npu_pct > 0:
        st.metric("NPU", f"{npu_pct:.0f}%")
        st.progress(min(npu_pct / 100.0, 1.0))

    # ---- Timestamp ---------------------------------------------------------
    st.caption(f"Updated: {snap.fetched_at.strftime('%H:%M:%S')}")


# ---------------------------------------------------------------------------
# render_backend_recommendations_card
# ---------------------------------------------------------------------------


def render_backend_recommendations_card(
    recommendations: dict[str, BackendRecommendation],
) -> None:
    """Display backend recommendations per role as a compact expander list.

    Each role gets an expander showing the ordered backend preference and
    a plain-text rationale.

    Args:
        recommendations: Dict mapping role name to
            :class:`BackendRecommendation`, as returned by
            :func:`~swarmmind.core.hardware.recommend_backends`.
    """
    import streamlit as st

    if not recommendations:
        st.caption("No backend recommendations yet. Run a swarm query first.")
        return

    target_icon = icon_html("crosshair", "regular", "1.2em")
    st.markdown(f"**{target_icon} Backend Strategy**", unsafe_allow_html=True)

    _BACKEND_ICONS: dict[LemonadeBackend, str] = {
        LemonadeBackend.ROCM: icon_html("lightning", "fill", "1.2em"),
        LemonadeBackend.VULKAN: icon_html("stack", "regular", "1.2em"),
        LemonadeBackend.CPU: icon_html("cpu", "regular", "1.2em"),
        LemonadeBackend.RYZEN_AI: icon_html("microchip", "fill", "1.2em"),
        LemonadeBackend.FASTFLOWLM: icon_html("lightning", "regular", "1.2em"),
        LemonadeBackend.AUTO: icon_html("gear", "regular", "1.2em"),
    }

    for role, rec in recommendations.items():
        label = role.capitalize()
        backend_icons_str = " → ".join(
            f"{_BACKEND_ICONS.get(b, '')} **{b.value.upper()}**"
            for b in rec.recommended_backends
        )

        with st.expander(label, expanded=False):
            st.markdown(
                f"**Recommended:**  \n{backend_icons_str}  \n\n"
                f"*{rec.rationale}*",
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_model_stats(
    stats: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Extract per-model metrics from the stats dict.

    Handles three common shapes:

    * ``{"model_name": {"tokens_per_second": …, …}}``
    * ``{"models": {"model_name": …}}``
    * ``{"data": [{"model": "name", …}, …]}``
    * A flat dict with top-level token/ttft fields (fallback).
    """
    models: dict[str, dict[str, Any]] = {}
    if not stats:
        return models

    source = stats.get("models", stats.get("data", stats))

    if isinstance(source, dict):
        for key, val in source.items():
            if isinstance(val, dict) and any(
                k in val for k in ("tokens_per_second", "tokens/sec", "tps", "ttft_ms", "ttft")
            ):
                models[str(key)] = {
                    "tokens_per_second": _safe_float(val, "tokens_per_second", "tokens/sec", "tps"),
                    "prompt_tokens": int(_safe_float(val, "prompt_tokens", "prompt_tokens_count")),
                    "completion_tokens": int(_safe_float(val, "completion_tokens", "completion_tokens_count")),
                    "ttft_ms": _safe_float(val, "ttft_ms", "ttft", "time_to_first_token"),
                }
    elif isinstance(source, list):
        for item in source:
            if isinstance(item, dict):
                name = (
                    item.get("model")
                    or item.get("id")
                    or item.get("name")
                    or ""
                )
                if name:
                    models[str(name)] = {
                        "tokens_per_second": _safe_float(
                            item, "tokens_per_second", "tokens/sec", "tps",
                        ),
                        "prompt_tokens": int(
                            _safe_float(item, "prompt_tokens", "prompt_tokens_count"),
                        ),
                        "completion_tokens": int(
                            _safe_float(item, "completion_tokens", "completion_tokens_count"),
                        ),
                        "ttft_ms": _safe_float(item, "ttft_ms", "ttft", "time_to_first_token"),
                    }

    # Fallback: if the dict itself looks like a single-model stat block
    if not models and any(
        k in stats for k in ("tokens_per_second", "tokens/sec", "ttft_ms", "ttft")
    ):
        models["default"] = {
            "tokens_per_second": _safe_float(stats, "tokens_per_second", "tokens/sec", "tps"),
            "prompt_tokens": int(_safe_float(stats, "prompt_tokens", "prompt_tokens_count")),
            "completion_tokens": int(_safe_float(stats, "completion_tokens", "completion_tokens_count")),
            "ttft_ms": _safe_float(stats, "ttft_ms", "ttft", "time_to_first_token"),
        }

    return models


def _safe_float(d: dict[str, Any], *keys: str) -> float:
    """Return the first numeric value found for *keys* in *d*.

    Returns ``0.0`` if no key matches or the value is not coercible.
    """
    for key in keys:
        val = d.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return 0.0
