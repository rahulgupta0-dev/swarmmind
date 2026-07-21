"""Chat / query panel for the SwarmMind UI with real-time swarm execution."""

from __future__ import annotations

import asyncio
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
}


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

    with st.expander("Swarm Configuration", expanded=False):
        web_search = st.checkbox(
            "Web Search", value=True, key="chat_web_search",
        )
        worker_types = st.multiselect(
            "Worker Types",
            ["RAG", "Web", "Analysis", "Code"],
            default=["RAG", "Web", "Analysis"],
            key="chat_worker_types",
        )
        model = st.selectbox(
            "Model",
            [config_obj.models.conductor, config_obj.models.worker]
            if config_obj else ["Gemma-4-12B-it"],
            key="chat_model",
        )

    # ------------------------------------------------------------------
    # Execute swarm
    # ------------------------------------------------------------------
    if launch and query.strip():
        _run_swarm(st, state, query.strip(), web_search, model)

    # ------------------------------------------------------------------
    # Display last report with action buttons
    # ------------------------------------------------------------------
    last_report = state.get("last_report")
    if last_report:
        st.divider()
        _render_action_buttons(st, state, last_report)
        _render_report(st, last_report)
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
# Swarm execution
# ---------------------------------------------------------------------------


def _run_swarm(
    st: Any,
    state: dict[str, Any],
    query: str,
    web_search: bool,
    model: str,
) -> None:
    """Execute the orchestrator with real-time progress updates."""
    config_obj = state["config"]
    client = state["client"]
    chroma = state.get("chroma")

    if not client:
        st.markdown(
            '<div class="amd-banner warning">'
            '<span class="icon"><i class="ph-bold ph-warning-octagon"></i></span>'
            '<span>Lemonade client is not available. Check connection.</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # Worker tracking state
    tasks: list[dict[str, str]] = []
    worker_status: dict[int, dict[str, str]] = {}
    worker_placeholder = st.empty()

    with st.status("Swarm in progress…", expanded=True) as status:

        def progress_callback(p_status: str, detail: dict[str, Any]) -> None:
            """Called by the orchestrator at each phase."""
            nonlocal tasks, worker_status

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
                    res = asyncio.run(
                        local_orch.run(
                            query=query,
                            project_context=project_ctx,
                            web_search_enabled=web_search,
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
            entry: dict[str, Any] = {
                "query": query,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "worker_count": len(worker_status),
                "report": report,
            }
            history: list[dict[str, Any]] = state.get("conversation_history", [])
            history.append(entry)
            st.session_state.conversation_history = history
            state["conversation_history"] = history

            st.rerun()

        except ConnectionError as exc:
            st.markdown(
                f'<div class="amd-banner warning"><span class="icon"><i class="ph-bold ph-warning-octagon"></i></span>'
                f"<span>{exc}</span></div>",
                unsafe_allow_html=True,
            )
            status.update(label="Connection failed")
        except RuntimeError as exc:
            import sys; print(f"SWARM FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            status.update(label=f"Failed: {type(exc).__name__}", state="error")
            st.error(f"Error running swarm: {exc}")
        except Exception as exc:
            if type(exc).__name__ == "RerunException":
                raise
            import sys; print(f"SWARM FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            status.update(label=f"Failed: {type(exc).__name__}", state="error")
            st.markdown(
                f'<div class="amd-banner warning"><span class="icon"><i class="ph-bold ph-warning-octagon"></i></span>'
                f"<span>Unexpected error: {exc}</span></div>",
                unsafe_allow_html=True,
            )
            status.update(label="Swarm failed")


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
    cols = st.columns(4)
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


# ---------------------------------------------------------------------------
# Report display
# ---------------------------------------------------------------------------


def _render_report(st: Any, report: dict[str, Any]) -> None:
    """Render the structured report using markdown."""
    # Title
    title = report.get("title", "Research Report")
    st.markdown(f"## {title}")

    # Executive Summary
    summary = report.get("executive_summary", "")
    if summary:
        st.markdown("### Executive Summary")
        st.markdown(summary)

    # Sections
    for section in report.get("sections", []):
        heading = section.get("heading", "")
        content = section.get("content", "")
        if heading:
            st.markdown(f"### {heading}")
        if content:
            st.markdown(content)

    # Conclusion
    conclusion = report.get("conclusion", "")
    if conclusion:
        st.markdown("### Conclusion")
        st.markdown(conclusion)

    # Contradictions
    contradictions: list[str] = report.get("contradictions", [])
    if contradictions:
        st.markdown(f"### {icon_html('warning-octagon')} Contradictions", unsafe_allow_html=True)
        for c in contradictions:
            st.warning(c)

    # Follow-up questions
    follow_ups: list[str] = report.get("follow_up_questions", [])
    if follow_ups:
        st.markdown(f"### {icon_html('lightbulb')} Follow-up Questions", unsafe_allow_html=True)
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