"""Chat / query panel for the SwarmMind UI with real-time swarm execution."""

from __future__ import annotations

import asyncio
import base64
import html
import inspect
import json
import re
from datetime import datetime
from typing import Any

from swarmmind.core.hardware import SystemHardware
from swarmmind.core.orchestrator import Orchestrator
from swarmmind.ui.components.icons import icon_html

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WORKER_ICONS: dict[str, str] = {
    "rag": "books",
    "web": "globe",
    "analysis": "chart-line",
    "code": "code",
    "vision": "image",
}

_STATUS_SYMBOLS: dict[str, str] = {
    "pending": "hourglass",
    "running": "arrows-clockwise",
    "completed": "check-circle",
    "failed": "x-circle",
}

_WORKER_LABELS: dict[str, str] = {
    "rag": "RAG",
    "web": "Web",
    "analysis": "Analysis",
    "code": "Code",
    "vision": "Vision",
}

_CITATION_MARKER_RE = re.compile(r"\[(S\d+)\]")


# ---------------------------------------------------------------------------
# First-run onboarding — pure helpers shared with the main app
# ---------------------------------------------------------------------------

GETTING_STARTED_STEPS: tuple[str, ...] = (
    "**Start Lemonade** — run `lemonade-server start`. If you have no AMD "
    "hardware, run `python tests/mock_lemonade_server.py` instead.",
    "**Add a source** in the Sources panel — a PDF, URL, YouTube video, or "
    "pasted text. Add a PDF, URL, or note to ground your research.",
    "**Ask a question** in the Chat panel, then click a [S#] citation to see "
    "the evidence behind the answer.",
)
DOCTOR_HINT: str = "Run `swarmmind doctor` to diagnose setup"
TROUBLESHOOTING_DOC: str = "docs/TROUBLESHOOTING.md"

_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_INLINE_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


def onboarding_text(*, include_hint: bool = True) -> str:
    """Plain-Markdown text of the three numbered getting-started steps."""
    lines = ["**Getting started**", ""]
    lines += [f"{i}. {step}" for i, step in enumerate(GETTING_STARTED_STEPS, 1)]
    if include_hint:
        lines += ["", DOCTOR_HINT]
    return "\n".join(lines)


def onboarding_html(*, include_hint: bool = True) -> str:
    """Compact HTML version of the getting-started card body (for app.py)."""
    items = "".join(f"<li>{_inline_md(step)}</li>" for step in GETTING_STARTED_STEPS)
    hint = ""
    if include_hint:
        hint = f'<p class="onboarding-hint">{_inline_md(DOCTOR_HINT)}</p>'
    return f"<ol>{items}</ol>{hint}"


def onboarding_needed(
    *, connected: bool, source_count: int, dismissed: bool = False,
) -> bool:
    """Whether the first-run card should be visible.

    The card is an inline, non-modal element, so it never blocks the panels
    while visible. It is hidden once the app is fully usable (connected *and*
    sources exist) or when the user dismisses it.
    """
    if dismissed:
        return False
    return (not connected) or source_count <= 0


def actionable_connection_message(
    error: Any = None, *, stage: str = "connection",
) -> str:
    """Build an actionable, traceback-free message for a Lemonade failure.

    *error* is accepted so call sites can pass the caught exception, but its
    raw text is deliberately never shown in the UI.
    """
    _ = error  # intentionally not rendered — no raw exceptions in the UI
    label = (stage or "connection").strip()
    fallback_cmd = "python tests/mock_lemonade_server.py"
    return (
        f"SwarmMind could not complete the {label}: the local Lemonade server "
        "is not reachable. Start it with `lemonade-server start` (no AMD "
        f"hardware? run `{fallback_cmd}`). See `{TROUBLESHOOTING_DOC}` for "
        "diagnosis steps."
    )


# ---------------------------------------------------------------------------
# Citation / grounding rendering helpers (shared with Studio exports)
# ---------------------------------------------------------------------------


def _linked_citations(text: Any, known_ids: Any = None) -> str:
    """Link ``[S#]`` markers, skipping IDs that are not in *known_ids*.

    ``known_ids=None`` keeps the permissive behaviour used by plain Markdown
    exports. Report rendering passes the report's real source IDs so an
    unknown marker never becomes a dead in-page anchor.
    """
    if not isinstance(text, str):
        return "" if text is None else str(text)
    allowed = set(known_ids) if known_ids is not None else None

    def replace(match: re.Match[str]) -> str:
        sid = match.group(1)
        if allowed is not None and sid not in allowed:
            return f"[{sid}]"
        return f"[{sid}](#source-{sid})"

    return _CITATION_MARKER_RE.sub(replace, text)


def link_citations(text: Any) -> str:
    """Turn ``[S1]`` markers into keyboard-accessible in-page links."""
    return _linked_citations(text)


def known_source_ids(report: dict[str, Any]) -> frozenset[str]:
    """Return the source IDs that actually exist in *report*."""
    ids: set[str] = set()
    sources = report.get("sources")
    if isinstance(sources, list):
        for raw in sources:
            if isinstance(raw, dict):
                sid = str(raw.get("id", "") or "").strip()
                if sid:
                    ids.add(sid)
    return frozenset(ids)


def cite_markers(ids: Any, known_ids: Any = None) -> str:
    """Render a compact run of ``[S#]`` links, omitting unknown IDs."""
    if not isinstance(ids, (list, tuple, set, frozenset)):
        return ""
    allowed = set(known_ids) if known_ids is not None else None
    parts: list[str] = []
    for value in ids:
        sid = str(value).strip()
        if not sid or (allowed is not None and sid not in allowed):
            continue
        parts.append(f"[{_esc(sid)}](#source-{_esc(sid)})")
    return " ".join(parts)


def cite_anchor_html(ids: Any, known_ids: Any = None) -> str:
    """Render citation IDs as real ``<a>`` anchors for use inside HTML markup."""
    if not isinstance(ids, (list, tuple, set, frozenset)):
        return ""
    allowed = set(known_ids) if known_ids is not None else None
    parts: list[str] = []
    for value in ids:
        sid = str(value).strip()
        if not sid or (allowed is not None and sid not in allowed):
            continue
        parts.append(f'<a href="#source-{_esc(sid)}">[{_esc(sid)}]</a>')
    return " ".join(parts)


def grounding_tone(report: dict[str, Any]) -> str:
    """CSS tone for a report's grounding state: good / warn / bad."""
    grounding = report.get("grounding")
    grounding = grounding if isinstance(grounding, dict) else {}
    contradicted = int(grounding.get("contradicted", 0) or 0)
    unverified = int(grounding.get("unverified", 0) or 0)
    supported = int(grounding.get("supported", 0) or 0)
    total = int(grounding.get("total_claims", 0) or 0)
    if contradicted:
        return "bad"
    if not total or unverified or not supported:
        return "warn"
    return "good"


def claim_css(status: str) -> str:
    """Map a claim status to its styling class."""
    return {
        "supported": "claim-ok",
        "contradicted": "claim-contradicted",
    }.get(str(status).lower(), "claim-unverified")


def tts_available(state: dict[str, Any] | None) -> bool:
    """Whether local Lemonade text-to-speech can be used in this session."""
    return bool((state or {}).get("client"))


def grounding_text(report: dict[str, Any]) -> str:
    """Plain-text grounding summary for console/Markdown exports."""
    grounding = report.get("grounding")
    grounding = grounding if isinstance(grounding, dict) else {}
    total = grounding.get("total_claims", 0) or 0
    supported = grounding.get("supported", 0) or 0
    unverified = grounding.get("unverified", 0) or 0
    contradicted = grounding.get("contradicted", 0) or 0
    if not total:
        return "No claims were recorded — model knowledge may be unverified."
    parts = [f"{supported}/{total} claims supported", f"{unverified} unverified"]
    if contradicted:
        parts.append(f"{contradicted} contradicted")
    return " · ".join(parts)


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _inline_md(value: str) -> str:
    """Escape *value*, then render the small Markdown subset used onboarding."""
    safe = _esc(value)
    safe = _INLINE_CODE_RE.sub(r"<code>\1</code>", safe)
    return _INLINE_BOLD_RE.sub(r"<strong>\1</strong>", safe)


def source_cards_html(sources: Any, *, heading: str = "Sources") -> str:
    """Render citable source cards with stable ``#source-S1`` anchors.

    Each card exposes title, kind, URL/path and the exact snippet used. Model
    knowledge is visually and textually marked as unverified.
    """
    if not isinstance(sources, list):
        sources = []
    if not sources:
        return (
            '<div class="source-empty" role="status">'
            "No sources were retrieved for this query. Add a PDF, URL, or note "
            "to ground your research."
            "</div>"
        )
    cards: list[str] = [f'<section class="source-cards" aria-label="{_esc(heading)}">']
    for raw in sources:
        src = raw if isinstance(raw, dict) else (
            {"url": str(raw), "title": str(raw), "kind": "web"} if isinstance(raw, str) else {}
        )
        sid = _esc(src.get("id", "") or "S?")
        kind = _esc(src.get("kind", "model") or "model")
        title = _esc(src.get("title") or src.get("url") or "Untitled source")
        url = _esc(src.get("url", ""))
        snippet = _esc(src.get("snippet", ""))
        unverified = kind == "model" or not url
        css = "source-card source-unverified" if unverified else "source-card"
        label = "model knowledge — unverified" if unverified else kind
        cards.append(
            f'<article class="{css}" id="source-{sid}" tabindex="0" role="group" '
            f'aria-label="Source {sid}: {title}">'
            f'<div class="source-card-head"><span class="source-id">[{sid}]</span>'
            f'<span class="source-kind">{_esc(label)}</span></div>'
            f'<div class="source-title">{title}</div>'
            + (f'<div class="source-url">{url}</div>' if url else "")
            + (f'<blockquote class="source-snippet">{snippet}</blockquote>' if snippet else "")
            + "</article>"
        )
    cards.append("</section>")
    return "\n".join(cards)


def build_sources_appendix(report: dict[str, Any]) -> list[str]:
    """Markdown Sources appendix (inline citations remain in section text)."""
    lines = ["## Sources", ""]
    sources = report.get("sources") or []
    if not isinstance(sources, list) or not sources:
        lines += ["_No sources were retrieved for this query._", ""]
        return lines
    for raw in sources:
        src = raw if isinstance(raw, dict) else {"url": str(raw), "title": str(raw)}
        sid = str(src.get("id", "S?"))
        title = src.get("title") or src.get("url") or "Untitled source"
        kind = src.get("kind", "model")
        url = src.get("url", "")
        snippet = " ".join(str(src.get("snippet", "")).split())
        lines.append(f"- **[{sid}] {title}** — `{kind}`" + (f" — {url}" if url else ""))
        if snippet:
            lines.append(f"  > {snippet}")
    lines.append("")
    return lines


def build_manifest_lines(report: dict[str, Any], config_obj: Any = None) -> list[str]:
    """Markdown run manifest: models, backends, stop reason, rounds, timestamp."""
    lines = ["## Run Manifest", ""]
    lines.append(f"- Stop reason: `{report.get('stop_reason', 'n/a')}`")
    lines.append(f"- Rounds used: `{report.get('rounds_used', 1)}`")
    lines.append(f"- Timestamp: {report.get('timestamp', 'n/a')}")
    if report.get("query"):
        lines.append(f"- Query: {report['query']}")

    models: dict[str, str] = {}
    backends: dict[str, str] = {}
    if config_obj is not None:
        model_cfg = getattr(config_obj, "models", None)
        for role in ("conductor", "worker", "embeddings", "image", "tts", "omni"):
            value = getattr(model_cfg, role, "") if model_cfg is not None else ""
            if value:
                models[role] = str(value)
            getter = getattr(config_obj, "backend_for", None)
            if callable(getter):
                try:
                    backend = getter(role)
                except Exception:
                    backend = None
                if backend:
                    backends[role] = str(backend)
    if models:
        lines.append("- Models: " + ", ".join(f"{k}=`{v}`" for k, v in models.items()))
    if backends:
        lines.append("- Backends: " + ", ".join(f"{k}=`{v}`" for k, v in backends.items()))
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------


def render_chat_panel(st: Any, state: dict[str, Any]) -> None:
    """Render the centre chat panel with query input and conversation history.

    Args:
        st: The ``streamlit`` module.
        state: Session-state-like dict containing conversation history,
               config, client, chroma, etc.
    """
    st.markdown(f"### {icon_html('chats-circle')} Research Query", unsafe_allow_html=True)

    # Load persisted conversations from the DB (once per project selection)
    _load_past_conversations(st, state)

    # First-run guide — only when there is no report and no history to show.
    render_onboarding_empty_state(st, state)

    # Grounded empty state — tell new users how to ground a research run.
    _render_grounded_empty_state(st, state)

    # ------------------------------------------------------------------
    # Query input section
    # ------------------------------------------------------------------
    query = st.text_area(
        label="Your research question",
        placeholder="Ask a research question…",
        key="chat_query_input",
        label_visibility="collapsed",
        height=100,
    )

    vision_image = st.file_uploader(
        "Upload an image for Vision worker (optional)",
        type=["png", "jpg", "jpeg"],
        key="chat_vision_upload"
    )
    if vision_image is not None:
        st.image(vision_image, caption="Uploaded Image", width=200)

    col1, col2 = st.columns([1, 3])
    with col1:
        launch = st.button(
            "Launch Swarm",
            type="primary",
            use_container_width=True,
        )
    with col2:
        pass  # reserved for status hint

    # ------------------------------------------------------------------
    # Swarm configuration expander
    # ------------------------------------------------------------------
    config_obj = state.get("config")

    web_cfg = getattr(config_obj, "web", None)
    privacy_allows_web = bool(getattr(web_cfg, "fetch_pages", True))

    with st.expander("Swarm Configuration", expanded=False):
        web_search = st.checkbox(
            "Web Search",
            value=privacy_allows_web,
            key="chat_web_search",
            disabled=not privacy_allows_web,
        )
        if not privacy_allows_web:
            st.caption(
                "External lookups are disabled in Settings → Privacy. "
                "Enable page fetching to use the Web worker."
            )
        worker_types = st.multiselect(
            "Worker Types",
            ["RAG", "Web", "Analysis", "Code", "Vision"],
            default=["RAG", "Web", "Analysis", "Vision"],
            key="chat_worker_types",
        )
        model = st.selectbox(
            "Model",
            [config_obj.models.conductor, config_obj.models.worker]
            if config_obj else ["Gemma-4-12B-it"],
            key="chat_model",
        )

    # Privacy notice — keep the "100% local" claim honest.
    if web_search and privacy_allows_web:
        st.caption("External lookup enabled — web pages may be fetched during this run.")

    # ------------------------------------------------------------------
    # Execute swarm
    # ------------------------------------------------------------------
    if launch and query.strip():
        import base64
        vision_base64 = None
        if vision_image is not None:
            image_bytes = vision_image.getvalue()
            vision_base64 = f"data:{vision_image.type};base64," + base64.b64encode(image_bytes).decode('utf-8')

        _run_swarm(
            st, state, query.strip(),
            web_search and privacy_allows_web, model, vision_base64,
        )

    # ------------------------------------------------------------------
    # Display last report with action buttons
    # ------------------------------------------------------------------
    last_report = state.get("last_report")
    if last_report:
        st.divider()
        _render_action_buttons(st, state, last_report)
        _render_report(st, last_report, state)
        _render_amd_metrics_section(st, state)
    # ------------------------------------------------------------------
    # Conversation history
    # ------------------------------------------------------------------
    st.divider()
    st.markdown(f"### {icon_html('scroll')} History", unsafe_allow_html=True)
    history: list[dict[str, Any]] = state.get("conversation_history", [])
    if not history:
        st.caption("No conversations yet. Launch a swarm above to get started.")

    for i, entry in enumerate(reversed(history)):
        ts = entry.get("timestamp", "")
        q = entry.get("query", "")
        wc = entry.get("worker_count", 0)
        label = q[:60] + ("…" if len(q) > 60 else "")
        if st.button(
            f"**{label}**  \n<small>{ts} · {wc} workers</small>",
            key=f"hist_{i}",
            use_container_width=True,
        ):
            report = entry.get("report")
            if report:
                st.session_state.last_report = report
                state["last_report"] = report
                st.rerun()


# ---------------------------------------------------------------------------
# Grounded empty state / first-run guide
# ---------------------------------------------------------------------------


def needs_onboarding_empty_state(state: dict[str, Any] | None) -> bool:
    """True when the chat has neither a report nor any history to display."""
    state = state or {}
    return not state.get("last_report") and not state.get("conversation_history")


def render_onboarding_empty_state(st: Any, state: dict[str, Any] | None) -> bool:
    """Render the three-step getting-started card in an otherwise empty chat.

    Returns True when the card was rendered, False when there is already a
    report or conversation history to show.
    """
    if not needs_onboarding_empty_state(state):
        return False
    st.markdown(onboarding_text())
    return True


def _configured_source_count(state: dict[str, Any]) -> int:
    """Count sources attached to the current project (0 when unavailable)."""
    db = state.get("db")
    project = state.get("current_project")
    if not db or not project:
        return 0
    try:
        pid = project.id if hasattr(project, "id") else project.get("id", "")
        if not pid:
            return 0
        from swarmmind.core.async_utils import run_async  # noqa: PLC0415
        return len(run_async(db.list_sources(pid)) or [])
    except Exception:
        return 0


def _render_grounded_empty_state(st: Any, state: dict[str, Any]) -> None:
    """Explain how to ground research when no sources are configured."""
    if _configured_source_count(state):
        return
    st.markdown(
        '<div class="amd-banner warning" role="status" '
        'aria-label="No sources configured">'
        '<span class="icon"><i class="ph-bold ph-warning-octagon" aria-hidden="true"></i></span>'
        "<span><strong>No sources yet.</strong> Add a PDF, URL, or note to ground "
        "your research — otherwise answers rely on model knowledge only.</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    if st.button("Go to Sources", key="jump_to_sources_btn"):
        st.session_state.jump_to_sources = True
        st.rerun()


def _load_past_conversations(st: Any, state: dict[str, Any]) -> None:
    """Load the current project's persisted conversations into session history.

    Runs once per project selection — the ``conversations_loaded_for`` marker
    lives in ``st.session_state`` so it survives widget reruns. When the
    selected project changes, the history is reset to that project's DB
    conversations (plus any in-session entries not yet persisted), so history
    never mixes reports across projects.
    """
    db = state.get("db")
    proj = state.get("current_project")
    if not db or not proj:
        return
    pid = proj.id if hasattr(proj, "id") else proj.get("id", "")
    if not pid:
        return
    if st.session_state.get("conversations_loaded_for") == pid:
        return

    try:
        from swarmmind.core.async_utils import run_async
        convs = run_async(db.list_conversations(pid))
    except Exception:
        return

    existing: list[dict[str, Any]] = state.get("conversation_history", [])
    # Keep in-session entries of this project that were never persisted
    keep = [
        e for e in existing
        if not e.get("_db_id") and e.get("_project_id") == pid
    ]
    have_ids = {e["_db_id"] for e in existing if e.get("_db_id")}

    loaded: list[dict[str, Any]] = []
    for c in reversed(convs):  # list_conversations is newest-first -> oldest-first
        if c.id in have_ids:
            continue
        report = None
        if c.report_json:
            try:
                report = json.loads(c.report_json)
            except Exception:
                report = None
        loaded.append({
            "_db_id": c.id,
            "_project_id": pid,
            "query": c.query,
            "timestamp": c.created_at.strftime("%Y-%m-%d %H:%M"),
            "worker_count": c.worker_count,
            "web_search_used": c.web_search_used,
            "report": report,
        })

    merged = keep + loaded
    st.session_state.conversation_history = merged
    state["conversation_history"] = merged
    st.session_state.conversations_loaded_for = pid


# ---------------------------------------------------------------------------
# Swarm execution
# ---------------------------------------------------------------------------


def _collect_live_sources(
    store: dict[str, dict[str, Any]], incoming: Any,
) -> None:
    """Merge newly-discovered sources into *store*, keyed by stable ID."""
    try:
        from swarmmind.core.synthesis import build_source_registry  # noqa: PLC0415
        registry, _ = build_source_registry([{"sources_cited": incoming}])
    except Exception:
        return
    for record in registry:
        sid = str(record.get("id", ""))
        if sid:
            store.setdefault(sid, record)


def _render_live_sources(
    placeholder: Any, store: dict[str, dict[str, Any]],
) -> None:
    """Update the sources-as-they-arrive placeholder."""
    if not store:
        return
    placeholder.markdown(
        "**Sources found so far**\n\n"
        + source_cards_html(list(store.values()), heading="Live sources"),
        unsafe_allow_html=True,
    )


def _run_swarm(
    st: Any,
    state: dict[str, Any],
    query: str,
    web_search: bool,
    model: str,
    vision_base64: str | None = None,
) -> None:
    """Execute the orchestrator with real-time progress updates."""
    config_obj = state["config"]
    client = state["client"]
    chroma = state.get("chroma")

    if not client:
        st.markdown(actionable_connection_message(stage="client setup"))
        return

    # Worker + live-source tracking state
    tasks: list[dict[str, str]] = []
    worker_status: dict[int, dict[str, str]] = {}
    live_sources: dict[str, dict[str, Any]] = {}
    worker_placeholder = st.empty()
    sources_placeholder = st.empty()
    sources_placeholder.markdown(
        '<div class="live-sources" role="status">'
        "Sources will appear here as workers find them…</div>",
        unsafe_allow_html=True,
    )

    with st.status("Swarm in progress…", expanded=True) as status:

        def progress_callback(p_status: str, detail: dict[str, Any]) -> None:
            """Called by the orchestrator at each phase."""
            nonlocal tasks, worker_status

            # Show sources as soon as a worker reports them (WP2 emits these
            # under ``sources``/``sources_cited``; older workers emit nothing).
            incoming = detail.get("sources") or detail.get("sources_cited") or []
            if incoming:
                _collect_live_sources(live_sources, incoming)
                _render_live_sources(sources_placeholder, live_sources)

            if p_status == "preflight":
                status.update(label="Checking connection…")

            elif p_status == "conducting":
                status.update(label="Conducting: Decomposing query…")

            elif p_status == "decomposed":
                tasks = detail.get("tasks", [])
                count = detail.get("task_count", len(tasks))
                status.update(
                    label=f"Analyzed into {count} sub-tasks",
                )
                # Initialise worker tracking rows
                for i, t in enumerate(tasks):
                    wt = t.get("worker_type", "analysis")
                    worker_status[i] = {
                        "type": wt,
                        "desc": t.get("task", ""),
                        "state": "pending",
                    }
                _render_worker_rows(worker_placeholder, worker_status)

            elif p_status == "working":
                status.update(label="Workers Running…")
                for k in worker_status:
                    if worker_status[k]["state"] == "pending":
                        worker_status[k]["state"] = "running"
                _render_worker_rows(worker_placeholder, worker_status)

            elif p_status == "completed_workers":
                count = detail.get("worker_count", 0)
                status.update(label=f"{count} Workers completed")
                for k in worker_status:
                    if worker_status[k]["state"] != "failed":
                        worker_status[k]["state"] = "completed"
                _render_worker_rows(worker_placeholder, worker_status)

            elif p_status == "synthesising":
                status.update(label="Synthesis: Merging all findings…")

            elif p_status == "done":
                status.update(label="Research complete!")

        # ---- Sync execution mode from UI settings to config ----
        exec_mode = st.session_state.get("execution_mode", "parallel")
        max_concurrent = st.session_state.get("max_concurrent_workers", 4)
        from swarmmind.config import ExecutionConfig
        config_obj.execution = ExecutionConfig(
            mode=exec_mode,
            max_concurrent=max_concurrent if exec_mode == "parallel" else 1,
        )

        # ---- Run orchestrator (blocking) ----
        try:
            orchestrator = Orchestrator(
                config_obj, client, chroma_store=chroma,
            )
            project_ctx: dict[str, Any] | None = None
            cur_proj = state.get("current_project")
            if cur_proj:
                pid = cur_proj.id if hasattr(cur_proj, "id") else cur_proj.get("id", "")
                project_ctx = {"id": pid}

            import threading
            import time
            from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

            ctx = get_script_run_ctx()
            result_container = []

            def worker_thread():
                add_script_run_ctx(threading.current_thread(), ctx)
                
                from swarmmind.rag.chroma_store import ChromaStore
                from pathlib import Path
                from swarmmind.core.orchestrator import Orchestrator
                
                try:
                    local_chroma = ChromaStore(str(Path.home() / ".swarmmind" / "chroma_db"))
                    local_orch = Orchestrator(config_obj, client, chroma_store=local_chroma)
                    
                    import asyncio
                    hist = state.get("conversation_history", [])
                    conv_context = [
                        {"query": e.get("query", ""),
                         "report": e.get("report")}
                        for e in hist
                    ] if hist else None
                    res = asyncio.run(
                        local_orch.run(
                            query=query,
                            project_context=project_ctx,
                            web_search_enabled=web_search,
                            vision_base64=vision_base64,
                            conversation_history=conv_context,
                            progress_callback=progress_callback,
                        )
                    )
                    result_container.append(res)
                except Exception as e:
                    import traceback
                    with open("demo_output/error.log", "a") as f:
                        f.write(f"THREAD EXCEPTION: {traceback.format_exc()}\n")
                    result_container.append(e)

            t = threading.Thread(target=worker_thread)
            t.start()

            while t.is_alive():
                time.sleep(0.1)

            t.join()
            report = result_container[0]
            if isinstance(report, Exception):
                with open("demo_output/error.log", "a") as f:
                    f.write(f"MAIN THREAD RAISED EXCEPTION: {report}\n")
                raise report

            # Persist the report
            state["last_report"] = report
            st.session_state.last_report = report

            # Add to conversation history
            proj_entry = state.get("current_project")
            pid_entry = (proj_entry.id if hasattr(proj_entry, "id")
                         else proj_entry.get("id", "")) if proj_entry else ""
            entry: dict[str, Any] = {
                "query": query,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "worker_count": len(worker_status),
                "report": report,
                "_project_id": pid_entry,
            }
            history: list[dict[str, Any]] = state.get("conversation_history", [])
            history.append(entry)
            st.session_state.conversation_history = history
            state["conversation_history"] = history

            # ---- Persist report to DB for cross-session durability ----
            try:
                from swarmmind.data.models import Conversation
                from swarmmind.core.async_utils import run_async
                db_save = state.get("db")
                proj_save = state.get("current_project")
                if db_save and proj_save:
                    pid_save = (proj_save.id if hasattr(proj_save, "id")
                                else proj_save.get("id", ""))
                    if pid_save:
                        conv = Conversation(
                            project_id=pid_save,
                            query=query,
                            web_search_used=web_search,
                            worker_count=len(worker_status),
                            report_json=json.dumps(report),
                        )
                        saved_conv = run_async(db_save.create_conversation(conv))
                        if saved_conv is not None:
                            entry["_db_id"] = saved_conv.id
            except Exception:
                pass

            st.rerun()

        except ConnectionError as exc:
            # Friendly, actionable guidance only — never echo the raw error.
            st.markdown(actionable_connection_message(exc, stage="swarm run"))
            status.update(label="Connection failed")
        except RuntimeError as exc:
            import sys; print(f"SWARM FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            status.update(label=f"Failed: {type(exc).__name__}", state="error")
            st.error("The swarm run failed. " + actionable_connection_message(exc, stage="swarm run"))
        except Exception as exc:
            if type(exc).__name__ == "RerunException":
                raise
            import sys; print(f"SWARM FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            status.update(label="Swarm failed", state="error")
            st.markdown(actionable_connection_message(exc, stage="swarm run"))


# ---------------------------------------------------------------------------
# Worker rows rendering  (used inside progress_callback)
# ---------------------------------------------------------------------------


def _render_worker_rows(
    placeholder: Any, worker_status: dict[int, dict[str, str]], elapsed: float = 0.0,
) -> None:
    """Fill *placeholder* with a visible worker-status table."""
    lines: list[str] = []
    for idx in sorted(worker_status):
        ws = worker_status[idx]
        wt = ws["type"]
        icon_name = _WORKER_ICONS.get(wt, "question")
        label = _WORKER_LABELS.get(wt, wt.upper())
        desc = ws.get("desc", "")[:65]
        state = ws.get("state", "pending")
        state_label = {"pending": "Waiting", "running": "Running", "completed": "Done", "failed": "Error"}.get(state, state)
        badge = f"*{state_label}*"
        icon_markup = f"{icon_html(icon_name, 'regular', '1.2em')}"
        line = f"{icon_markup} **{label}**: {desc[:50]} — {badge}"
        lines.append(line)

    if not lines:
        body = "Preparing swarm…"
    else:
        ts = f" \n_Elapsed: {elapsed:.1f}s_"
        body = "\n".join(lines) + ts

    placeholder.markdown(body, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Action buttons
# ---------------------------------------------------------------------------


def _render_action_buttons(
    st: Any,
    state: dict[str, Any],
    report: dict[str, Any],
) -> None:
    """Render the action button row below a completed report."""
    cols = st.columns(5)
    with cols[0]:
        if st.button(
	            "Save to Notes",
            key="save_notes_btn",
            use_container_width=True,
        ):
            _save_report_as_note(st, state, report)
    with cols[1]:
        if st.button(
	            "Generate Diagram",
            key="gen_diagram_btn",
            use_container_width=True,
        ):
            state["studio_action"] = "diagram"
    with cols[2]:
        if st.button(
	            "Narrate",
            key="narrate_btn",
            use_container_width=True,
        ):
            state["studio_action"] = "narration"
    with cols[3]:
        st.download_button(
	            "Export MD",
            data=_report_to_markdown(report),
            file_name="swarmmind_report.md",
            mime="text/markdown",
            key="download_md_btn",
            use_container_width=True,
        )
    # Read aloud is local-only: it needs a Lemonade client. Show an obvious
    # disabled state (with a short explanation) when TTS is unavailable.
    tts_ready = tts_available(state)
    with cols[4]:
        if st.button(
            "Read aloud",
            key="read_aloud_btn",
            use_container_width=True,
            disabled=not tts_ready,
            help=(
                "Speak this report with your local Lemonade TTS voice."
                if tts_ready else
                "Connect to a local Lemonade server to enable read aloud."
            ),
        ):
            st.caption("Preparing audio…")
            _read_aloud_report(st, state, report)
        if not tts_ready:
            st.markdown(
                '<div class="action-hint">Read aloud needs a local TTS connection.</div>',
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Read aloud — opt-in TTS via the local Lemonade server
# ---------------------------------------------------------------------------


def _report_read_aloud_text(report: dict[str, Any] | None) -> str:
    """Flatten a report into plain prose suitable for text-to-speech.

    Citation markers (``[S1]``) are removed so the narration does not read
    them out loud. Output is capped to keep TTS requests reasonable.
    """
    if not isinstance(report, dict):
        return ""
    parts: list[str] = []
    for key in ("title", "executive_summary"):
        value = report.get(key, "")
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    for section in report.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        for key in ("heading", "content"):
            value = section.get(key, "")
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    conclusion = report.get("conclusion", "")
    if isinstance(conclusion, str) and conclusion.strip():
        parts.append(conclusion.strip())
    text = "\n\n".join(parts)
    return _CITATION_MARKER_RE.sub("", text).strip()[:4000]


def _tts_audio_bytes(result: Any) -> bytes:
    """Normalise TTS output: raw bytes, or a base64 dict from older clients."""
    if isinstance(result, (bytes, bytearray)):
        return bytes(result)
    if isinstance(result, dict):
        encoded = result.get("b64_json") or result.get("audio")
        data = result.get("data")
        if not encoded and isinstance(data, list) and data:
            encoded = data[0].get("b64_json") if isinstance(data[0], dict) else None
        if isinstance(encoded, str):
            try:
                return base64.b64decode(encoded)
            except Exception:
                return b""
    return b""


def _audio_data_uri(audio_bytes: bytes) -> str:
    """Build a base64 WAV/MP3 data URI for ``st.audio`` (no temp files)."""
    if audio_bytes[:3] == b"ID3" or audio_bytes[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        mime = "audio/mpeg"
    else:
        mime = "audio/wav"  # RIFF/WAVE header and any unknown binary default to WAV
    encoded = base64.b64encode(audio_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _read_aloud_report(
    st: Any,
    state: dict[str, Any] | None,
    report: dict[str, Any] | None,
) -> None:
    """Speak the current report aloud using Lemonade's local TTS endpoint.

    This is strictly opt-in (a button press) and stays inside the "100% local"
    privacy story: audio is synthesised by the user's own Lemonade server, so
    text never leaves the machine and no cloud TTS service is contacted.
    """
    state = state or {}
    client = state.get("client")
    if not client or not report:
        st.warning("Read aloud needs a Lemonade connection and a report first.")
        return

    text = _report_read_aloud_text(report)
    if not text:
        st.warning("There is nothing in this report to read aloud.")
        return

    config_obj = state.get("config")
    models = getattr(config_obj, "models", None)
    model = getattr(models, "tts", None) or None

    try:
        result = client.text_to_speech(text=text, model=model, voice="af")
        if inspect.isawaitable(result):
            result = asyncio.run(result)
    except Exception as exc:
        st.warning(f"Read aloud is unavailable right now (local Lemonade TTS): {exc}")
        return

    audio_bytes = _tts_audio_bytes(result)
    if not audio_bytes:
        st.warning("Read aloud returned no audio data.")
        return

    st.audio(_audio_data_uri(audio_bytes))
    st.caption(
        "Read aloud runs on your local Lemonade server — nothing is sent to a cloud TTS service."
    )


# ---------------------------------------------------------------------------
# Report display
# ---------------------------------------------------------------------------


def _render_section_heading(st: Any, title: str, known: Any = None) -> None:
    """Render a consistent report section heading with live citation links."""
    def replace(match: re.Match[str]) -> str:
        sid = match.group(1)
        if known is not None and sid not in set(known):
            return f"[{_esc(sid)}]"
        return f'<a href="#source-{_esc(sid)}">[{_esc(sid)}]</a>'

    safe = _CITATION_MARKER_RE.sub(replace, _esc(title))
    st.markdown(f'<h3 class="report-section">{safe}</h3>', unsafe_allow_html=True)


def _render_grounding_badge(st: Any, report: dict[str, Any]) -> None:
    """Compact, colour-and-text grounding summary with accessible markup."""
    text = grounding_text(report)
    tone = grounding_tone(report)
    label = {"good": "supported", "warn": "unverified", "bad": "contradicted"}[tone]
    st.markdown(
        f'<div class="grounding-badge grounding-{tone}" role="status" '
        f'aria-label="Grounding {label}: {_esc(text)}">'
        f'<span class="badge-dot" aria-hidden="true"></span>'
        f'<span><strong>{label.title()}:</strong> {_esc(text)}</span></div>',
        unsafe_allow_html=True,
    )


def _render_run_manifest(
    st: Any, report: dict[str, Any], state: dict[str, Any] | None = None,
) -> None:
    """Run manifest + one-click query prefill for re-runs."""
    config_obj = (state or {}).get("config")
    lines = build_manifest_lines(report, config_obj)
    body = "\n".join(f"<div>{_esc(line)}</div>" for line in lines if line)
    st.markdown(
        '<details class="run-manifest">'
        '<summary>Run manifest · models, backends, stop reason</summary>'
        f'<div class="manifest-body">{body}</div></details>',
        unsafe_allow_html=True,
    )
    if st.button("Re-run this query", key="rerun_query_btn", use_container_width=True):
        st.session_state.chat_query_input = report.get("query", "") or ""
        st.rerun()


def _render_report(
    st: Any, report: dict[str, Any], state: dict[str, Any] | None = None,
) -> None:
    """Render the structured report with clickable citations and source cards."""
    title = report.get("title", "Research Report")
    known = known_source_ids(report)
    st.markdown(f"## {title}")
    # The one and only grounding summary line for this report.
    _render_grounding_badge(st, report)

    # Executive Summary
    summary = report.get("executive_summary", "")
    if summary:
        _render_section_heading(st, "Executive Summary", known)
        st.markdown(_linked_citations(summary, known))

    # Sections — only known [S#] markers become links to source cards below.
    for section in report.get("sections", []):
        if not isinstance(section, dict):
            continue
        heading = section.get("heading", "")
        content = section.get("content", "")
        if heading:
            _render_section_heading(st, heading, known)
        if content:
            st.markdown(_linked_citations(content, known))
        markers = cite_markers(section.get("sources") or [], known)
        if markers:
            st.markdown(f"**Cites:** {markers}")

    # Grounding detail — unverified claims are visually distinct.
    claims = report.get("claims") or []
    if isinstance(claims, list) and claims:
        with st.expander("Grounding details", expanded=False):
            for i, claim in enumerate(claims):
                if not isinstance(claim, dict):
                    continue
                status = str(claim.get("status", "unverified"))
                css = claim_css(status)
                label = status.replace("_", " ").capitalize()
                ids = cite_anchor_html(claim.get("source_ids", []), known)
                st.markdown(
                    f'<div class="claim-row {css}" role="listitem" '
                    f'aria-label="Claim {i + 1}: {label}">'
                    f'<span class="claim-status">{_esc(label)}</span> '
                    f"{_esc(claim.get('text', ''))} {ids}</div>",
                    unsafe_allow_html=True,
                )

    # Conclusion
    conclusion = report.get("conclusion", "")
    if conclusion:
        _render_section_heading(st, "Conclusion", known)
        st.markdown(_linked_citations(conclusion, known))

    # Contradictions
    contradictions: list[str] = report.get("contradictions", [])
    if contradictions:
        _render_section_heading(st, "Contradictions", known)
        for c in contradictions:
            st.warning(_linked_citations(c, known))

    # Source cards — anchored so inline citation links scroll here on click.
    _render_section_heading(st, "Sources", known)
    st.markdown(source_cards_html(report.get("sources") or []), unsafe_allow_html=True)

    # Run manifest + re-run prefill
    _render_run_manifest(st, report, state)

    # Follow-up questions
    follow_ups: list[str] = report.get("follow_up_questions", [])
    if follow_ups:
        _render_section_heading(st, "Follow-up Questions", known)
        fcols = st.columns(min(3, len(follow_ups)))
        for i, q in enumerate(follow_ups):
            col_idx = i % len(fcols)
            with fcols[col_idx]:
                if st.button(q, key=f"fu_{i}", use_container_width=True):
                    st.session_state.chat_query_input = q
                    st.rerun()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _report_to_markdown(report: dict[str, Any]) -> str:
    """Convert a report dict to a Markdown string."""
    lines: list[str] = [
        f"# {report.get('title', 'Research Report')}",
        "",
    ]

    summary = report.get("executive_summary", "")
    if summary:
        lines.append("## Executive Summary")
        lines.append(summary)
        lines.append("")

    for section in report.get("sections", []):
        heading = section.get("heading", "")
        content = section.get("content", "")
        if heading:
            lines.append(f"## {heading}")
        if content:
            lines.append(content)
        lines.append("")

    conclusion = report.get("conclusion", "")
    if conclusion:
        lines.append("## Conclusion")
        lines.append(conclusion)
        lines.append("")

    for c in report.get("contradictions", []):
        lines.append(f"- [!] {c}")
    if report.get("contradictions"):
        lines.append("")

    for q in report.get("follow_up_questions", []):
        lines.append(f"- {q}")
    if report.get("follow_up_questions"):
        lines.append("")

    lines.append("## Grounding")
    lines.append(grounding_text(report))
    lines.append("")
    lines.extend(build_sources_appendix(report))
    lines.extend(build_manifest_lines(report))

    return "\n".join(lines)


def _save_report_as_note(
    st: Any,
    state: dict[str, Any],
    report: dict[str, Any],
) -> None:
    """Save the current report to saved notes (session state or DB)."""
    title = report.get("title", "Untitled Report")[:80]
    content = _report_to_markdown(report)

    # Prefer database
    db = state.get("db")
    cur_proj = state.get("current_project")
    if db and cur_proj:
        proj_id = cur_proj.id if hasattr(cur_proj, "id") else cur_proj.get("id", "")
        if proj_id:
            from swarmmind.data.models import Note

            note = Note(
                project_id=proj_id,
                title=title,
                content=content,
            )
            try:
                from swarmmind.core.async_utils import run_async
                run_async(db.create_note(note))
                st.markdown(
                    '<div class="amd-banner success"><span class="icon"><i class="ph-bold ph-check-circle"></i></span>'
                    '<span>Report saved to notes (DB)!</span></div>',
                    unsafe_allow_html=True,
                )
                # Refresh notes in session state
                _refresh_notes(st, state, db, proj_id)
            except Exception as exc:
                st.warning(f"Could not save to DB ({exc}), using session state.")
                _save_to_session_notes(st, state, title, content)
            return

    # Fallback: session state
    _save_to_session_notes(st, state, title, content)


def _save_to_session_notes(
    st: Any,
    state: dict[str, Any],
    title: str,
    content: str,
) -> None:
    """Save a note to session state only."""
    if "saved_notes" not in st.session_state:
        st.session_state.saved_notes = []
    st.session_state.saved_notes.append({
        "title": title,
        "content": content,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    state["saved_notes"] = st.session_state.saved_notes
    st.markdown(
        '<div class="amd-banner success"><span class="icon"><i class="ph-bold ph-check-circle"></i></span>'
        '<span>Report saved to notes!</span></div>',
        unsafe_allow_html=True,
    )


def _refresh_notes(
    st: Any,
    state: dict[str, Any],
    db: Any,
    project_id: str,
) -> None:
    """Reload notes from DB into session state."""
    try:
        from swarmmind.core.async_utils import run_async
        notes = run_async(db.list_notes(project_id))
        session_notes = []
        for n in notes:
            session_notes.append({
                "title": n.title,
                "content": n.content,
                "date": n.created_at.strftime("%Y-%m-%d %H:%M") if hasattr(n, "created_at") else "",
            })
        st.session_state.saved_notes = session_notes
        state["saved_notes"] = session_notes
    except Exception:
        pass



# ---------------------------------------------------------------------------
# AMD Runtime Metrics section (shown after a completed query)
# ---------------------------------------------------------------------------


def _render_amd_metrics_section(st: Any, state: dict[str, Any]) -> None:
    """Render AMD hardware badges and runtime metrics dashboard.

    Shown after each completed swarm query.  Uses the cached hardware
    from session state to derive display labels and a live metrics
    dashboard from the Lemonade client.
    """
    hardware: SystemHardware | None = state.get("hardware")
    client = state.get("client")

    if hardware is None:
        return

    st.divider()

    # Hardware badges (from detected hardware, not hard-coded)
    badges = _hardware_badge_markdown(hardware)
    if badges:
        st.markdown(badges, unsafe_allow_html=True)

    # Collapsible runtime metrics dashboard
    if client:
        with st.expander("AMD Runtime Metrics", expanded=False):
            from swarmmind.ui.components.metrics import (  # noqa: PLC0415
                render_stats_dashboard,
            )

            render_stats_dashboard(client, refresh_seconds=2.0)



def _hardware_badge_markdown(hw: SystemHardware) -> str:
    """Build human-readable hardware badges from detected hardware.

    Labels are derived from ``hw.gpus[*].backend`` and
    ``hw.npus[*].backend`` — never hard-coded.

    Examples::

        Running on ROCm ... Radeon RX 7900 XTX
        Powered by Ryzen AI NPU
        Vulkan acceleration
    """
    parts: list[str] = []
    has_rocm = any(g.backend == "rocm" for g in hw.gpus)
    has_vulkan = any(g.backend == "vulkan" for g in hw.gpus)

    for gpu in hw.gpus:
        if gpu.backend == "rocm":
            parts.append(f'<i class="ph-bold ph-cpu"></i> Running on ROCm &middot; {gpu.name}')
        elif gpu.backend == "vulkan" and not has_rocm:
            parts.append(f'<i class="ph-bold ph-lightning"></i> Vulkan acceleration')

    for npu in hw.npus:
        if npu.available:
            if npu.backend == "ryzenai":
                parts.append(f'<i class="ph-bold ph-lightning"></i> Powered by Ryzen AI NPU')
            elif npu.backend == "fastflowlm":
                parts.append(f'<i class="ph-bold ph-lightning"></i> Powered by FastFlowLM NPU')

    if not parts and any(
        keyword in hw.cpu_name.lower()
        for keyword in ("amd", "ryzen")
    ):
        parts.append('<i class="ph-bold ph-monitor"></i> AMD CPU detected')

    return "<br>".join(parts)