"""Sources & settings panel for the SwarmMind UI."""

from __future__ import annotations

import asyncio
from typing import Any

from swarmmind.data.models import Project, Source
from swarmmind.ui.components.icons import icon_html

# ---------------------------------------------------------------------------
# Source-type icon mapping
# ---------------------------------------------------------------------------

_SOURCE_ICONS: dict[str, str] = {
    "pdf": "file-pdf",
    "youtube": "video",
    "web": "globe",
    "text": "file-text",
    "docx": "file-doc",
    "pptx": "file-pptx",
    "xlsx": "file-xls",
    "epub": "book",
    "csv": "table",
    "image": "image",
    "audio": "speaker-high",
    "json": "code",
    "xml": "code",
    "zip": "archive",
    "default": "file",
}

_STATUS_INDICATORS: dict[str, str] = {
    "pending": "hourglass",
    "processing": "arrows-clockwise",
    "ready": "check-circle",
    "error": "x-circle",
}

# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------


def render_sources_panel(st: Any, state: dict[str, Any]) -> None:
    """Render the left panel for source management and settings.

    Args:
        st: The ``streamlit`` module.
        state: Session-state-like dict with keys such as ``config``, ``db``,
            ``current_project``.
    """
    st.markdown(f"### {icon_html('books')} Sources", unsafe_allow_html=True)

    db = state.get("db")
    config_obj = state.get("config")

    # ------------------------------------------------------------------
    # Project selector
    # ------------------------------------------------------------------
    projects: list[Project] = []
    if db:
        try:
            from swarmmind.core.async_utils import run_async
            projects = run_async(db.list_projects())
        except Exception:
            projects = []

    project_names = [p.name for p in projects]
    project_ids = [p.id for p in projects]

    # Determine current selection index
    cur_idx = 0
    cur_proj = state.get("current_project")
    if cur_proj and project_ids:
        cid = cur_proj.id if hasattr(cur_proj, "id") else cur_proj.get("id", "")
        try:
            cur_idx = project_ids.index(cid)
        except ValueError:
            cur_idx = 0

    placeholder = "No projects — create one below"
    selected_name = st.selectbox(
        "Project",
        options=project_names if project_names else [placeholder],
        index=cur_idx if project_names else 0,
        key="project_selector",
    )

    # Sync selection back to state
    if project_names and selected_name and selected_name in project_names:
        idx = project_names.index(selected_name)
        proj = projects[idx]
        state["current_project"] = proj
        st.session_state.current_project = proj

    # ------------------------------------------------------------------
    # Create project — show as collapsed expander below the selector
    # ------------------------------------------------------------------
    # (shown whether or not projects exist, so users can always add more)
    create_expanded = not projects  # auto-expand only when there are no projects yet
    with st.expander("✚ Create Project", expanded=create_expanded):
        new_name = st.text_input("Project name", key="new_project_name", placeholder="e.g. AMD AI Research")
        new_desc = st.text_input("Description", key="new_project_desc", placeholder="Short project description")
        if st.button(
            "Create",
            type="primary",
            use_container_width=True,
            key="create_project_btn",
        ):
            if new_name and db:
                proj = Project(name=new_name, description=new_desc)
                try:
                    from swarmmind.core.async_utils import run_async
                    run_async(db.create_project(proj))
                    st.markdown(
                        f'<div class="amd-banner success"><span class="icon"><i class="ph-bold ph-check-circle"></i></span>'
                        f"<span>Project '{new_name}' created!</span></div>",
                        unsafe_allow_html=True,
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to create project: {exc}")

    if not projects:
        return  # nothing else to show without a project

    st.divider()

    # ------------------------------------------------------------------
    # Sources list for the current project
    # ------------------------------------------------------------------
    current_proj = state.get("current_project")
    if current_proj and db:
        proj_id = current_proj.id if hasattr(current_proj, "id") else current_proj.get("id", "")

        try:
            from swarmmind.core.async_utils import run_async
            sources: list[Source] = run_async(db.list_sources(proj_id))
        except Exception:
            sources = []

        if sources:
            st.markdown("**Project Sources**")
            for src in sources:
                _render_source_row(st, src)
        else:
            st.caption("No sources yet. Add one below.")

    st.divider()
    # ------------------------------------------------------------------
    # Add Source form
    # ------------------------------------------------------------------
    with st.expander("Add Source", expanded=False):
        _render_add_source_form(st, state, db)

    st.divider()
    st.markdown(f"### {icon_html('gear')} Settings", unsafe_allow_html=True)
    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    st.checkbox(
        "Enable web search",
        value=True,
        key="sources_web_search",
    )
    st.slider(
        "RAG top-K",
        min_value=1,
        max_value=20,
        value=config_obj.rag.top_k if config_obj else 5,
        key="sources_rag_top_k",
    )

    st.markdown(f"### {icon_html('arrows-clockwise')} Execution Mode", unsafe_allow_html=True)
    exec_mode = st.radio(
        "Worker execution",
        options=["parallel", "sequential"],
        index=0 if (config_obj and config_obj.execution.mode == "parallel") else 1,
        key="execution_mode",
        help="Parallel: faster on 32 GB+ RAM. Sequential: safer on 8-16 GB RAM.",
    )
    if exec_mode == "parallel":
        max_concurrent = st.slider(
            "Max concurrent workers",
            min_value=1,
            max_value=8,
            value=config_obj.execution.max_concurrent if config_obj else 4,
            key="max_concurrent_workers",
            help="Limit parallel workers to avoid OOM on smaller GPUs.",
        )
    else:
        max_concurrent = 1

    st.markdown(f"### {icon_html('crosshair')} Backend Strategy", unsafe_allow_html=True)
    # Backend Strategy (from hardware detection)
    # ------------------------------------------------------------------
    st.divider()
    _render_backend_strategy(st, state)


def _render_backend_strategy(st: Any, state: dict[str, Any]) -> None:
    """Show backend recommendations in a collapsible section.

    Computes per-role recommendations from the cached hardware snapshot
    (if available) using :func:`~swarmmind.core.hardware.recommend_backends`.
    """
    hardware = state.get("hardware")
    if hardware is None:
        st.caption("Run a swarm query first to detect hardware and see recommendations.")
        return

    from swarmmind.core.hardware import recommend_backends  # noqa: PLC0415
    from swarmmind.ui.components.metrics import (  # noqa: PLC0415
        render_hardware_card,
        render_backend_recommendations_card,
    )

    render_hardware_card(hardware)
    recommendations = recommend_backends(hardware)
    with st.expander("Backend Assignment per Role", expanded=False):
        render_backend_recommendations_card(recommendations)

# ---------------------------------------------------------------------------
# Add source form
# ---------------------------------------------------------------------------


def _render_add_source_form(
    st: Any,
    state: dict[str, Any],
    db: Any,
) -> None:
    """Render the inline form for adding a new source."""
    source_type_label = st.selectbox(
        "Source type",
        ["PDF", "YouTube", "Web URL", "Text"],
        key="add_source_type",
    )

    type_map = {
        "PDF": "pdf",
        "YouTube": "youtube",
        "Web URL": "web",
        "Text": "text",
    }
    db_type = type_map[source_type_label]

    placeholders = {
        "PDF": "/path/to/file.pdf  or  https://…",
        "YouTube": "https://youtube.com/watch?v=…",
        "Web URL": "https://…",
        "Text": "Paste text content here…",
    }

    if source_type_label == "Text":
        source_input = st.text_area(
            "Content",
            placeholder=placeholders[source_type_label],
            key="source_text_input",
        )
    else:
        source_input = st.text_input(
            "URL or path",
            placeholder=placeholders[source_type_label],
            key="source_url_input",
        )

    if st.button(
        "Add Source",
        type="primary",
        use_container_width=True,
        key="add_source_submit",
    ):
        _handle_add_source(st, state, db, db_type, source_input)


def _handle_add_source(
    st: Any,
    state: dict[str, Any],
    db: Any,
    db_type: str,
    source_input: str,
) -> None:
    """Validate and persist a new source."""
    if not source_input or not source_input.strip():
        st.warning("Please provide a source value.")
        return

    cur_proj = state.get("current_project")
    if not cur_proj or not db:
        st.warning("No project selected or database unavailable.")
        return

    proj_id = cur_proj.id if hasattr(cur_proj, "id") else cur_proj.get("id", "")
    if not proj_id:
        st.warning("Invalid project.")
        return

    # Derive a readable display name from the input
    display = source_input.strip()
    if "/" in display:
        display = display.rsplit("/", 1)[-1]
    if len(display) > 60:
        display = display[:57] + "…"

    src = Source(
        project_id=proj_id,
        source_type=db_type,
        source_uri=source_input.strip(),
        display_name=display,
    )

    try:
        from swarmmind.core.async_utils import run_async
        run_async(db.create_source(src))
        st.markdown(
            '<div class="amd-banner success"><span class="icon"><i class="ph-bold ph-check-circle"></i></span>'
            '<span>Source added!</span></div>',
            unsafe_allow_html=True,
        )
        st.rerun()
    except Exception as exc:
        st.error(f"Failed to add source: {exc}")


# ---------------------------------------------------------------------------
# Source row rendering
# ---------------------------------------------------------------------------


def _render_source_row(st: Any, source: Source) -> None:
    """Render a single source with type icon, name and status indicator."""
    icon_name = _SOURCE_ICONS.get(source.source_type, _SOURCE_ICONS["default"])
    indicator_icon_name = _STATUS_INDICATORS.get(source.status, "hourglass")
    indicator = icon_html(indicator_icon_name, "regular", "1.2em")
    icon_markup = icon_html(icon_name, "regular", "1.2em")
    display = source.display_name or source.source_uri[:40]

    col1, col2 = st.columns([4, 1])
    with col1:
        st.markdown(
            f"{icon_markup}  {display}",
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(indicator, unsafe_allow_html=True)
