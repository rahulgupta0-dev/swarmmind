"""Studio / action panel for the SwarmMind UI."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime
from typing import Any

from swarmmind.ui.components.icons import icon_html

# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------


def render_studio_panel(st: Any, state: dict[str, Any]) -> None:
    """Render the right panel with quick actions, notes, export and connection info.

    Args:
        st: The ``streamlit`` module.
        state: Session-state-like dict with keys such as ``config``, ``client``,
            ``last_report``, ``saved_notes``, ``connection_status``.
    """
    st.markdown(f"### {icon_html('palette')} Studio", unsafe_allow_html=True)

    config_obj = state.get("config")
    client = state.get("client")
    last_report: dict[str, Any] | None = state.get("last_report")
    has_report = last_report is not None

    # ------------------------------------------------------------------
    # Quick Actions
    # ------------------------------------------------------------------
    st.markdown("**Quick Actions**")

    handle_studio_action(st, state)

    if st.button(
        "Full Report",
        use_container_width=True,
        disabled=not has_report,
        key="full_report_btn",
    ):
        st.info("Report is displayed in the centre panel above.")

    if st.button(
        "Diagram",
        use_container_width=True,
        disabled=not has_report,
        key="diagram_btn",
    ):
        _generate_diagram(st, config_obj, client, last_report)

    if st.button(
        "Narration",
        use_container_width=True,
        disabled=not has_report,
        key="narration_btn",
    ):
        _generate_narration(st, config_obj, client, last_report)

    if st.button(
        "Summary",
        use_container_width=True,
        disabled=not has_report,
        key="summary_btn",
    ):
        _generate_summary(st, config_obj, client, last_report)

    st.divider()

    # ------------------------------------------------------------------
    # Saved Notes
    # ------------------------------------------------------------------
    st.markdown(f"### {icon_html('note-pencil')} Saved Notes", unsafe_allow_html=True)

    if st.button(
        "Save Current Report",
        use_container_width=True,
        disabled=not has_report,
        key="save_report_note_studio",
    ):
        _save_current_report(st, state, last_report)

    saved_notes: list[dict[str, str]] = state.get("saved_notes", [])
    if saved_notes:
        for i, note in enumerate(saved_notes):
            title = note.get("title", f"Note {i + 1}")
            date = note.get("date", "")
            with st.expander(
                f"{title[:50]}{'…' if len(title) > 50 else ''}  \n"
                f"<small>{date}</small>",
                key=f"studio_note_{i}",
            ):
                st.markdown(note.get("content", ""))
    else:
        st.caption("No saved notes yet. Save a report above.")

    st.divider()

    # ------------------------------------------------------------------
    # Export Section
    # ------------------------------------------------------------------
    st.markdown(f"### {icon_html('upload')} Export", unsafe_allow_html=True)

    if has_report:
        md_content = _report_to_markdown(last_report)
        html_content = _report_to_html(last_report)

        st.download_button(
            "Markdown",
            data=md_content,
            file_name="swarmmind_report.md",
            mime="text/markdown",
            use_container_width=True,
            key="export_md",
        )
        st.download_button(
            "HTML",
            data=html_content,
            file_name="swarmmind_report.html",
            mime="text/html",
            use_container_width=True,
            key="export_html",
        )
    else:
        st.caption("Run a swarm query first to enable exports.")

    st.divider()

    # ------------------------------------------------------------------
    # Lemonade Info
    # ------------------------------------------------------------------
    st.markdown(f"### {icon_html('link')} Lemonade", unsafe_allow_html=True)

    conn_status = state.get("connection_status", "unknown")
    if conn_status == "ok":
        st.markdown(
            f'<span style="color:green;">{icon_html("circle", "fill")}</span>'
            ' <span style="font-weight:600;">Connected</span>',
            unsafe_allow_html=True,
        )
    elif conn_status == "error":
        st.markdown(
            f'<span style="color:red;">{icon_html("circle", "fill")}</span>'
            ' <span style="font-weight:600;">Disconnected</span>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<span style="color:gray;">{icon_html("circle", "fill")}</span>'
            ' <span style="font-weight:600;">Unknown</span>',
            unsafe_allow_html=True,
        )

    if config_obj:
        st.code(
            f"Server: {config_obj.get_lemonade_base_url()}\n"
            f"Conductor: {config_obj.models.conductor}\n"
            f"Worker:    {config_obj.models.worker}\n"
            f"Embed:     {config_obj.models.embeddings}\n"
            f"Image:     {config_obj.models.image}\n"
            f"TTS:       {config_obj.models.tts}",
        )

    if st.button(
        "Clear Conversation",
        use_container_width=True,
        key="clear_conv_studio",
    ):
        st.session_state.conversation_history = []
        st.session_state.last_report = None
        state["conversation_history"] = []
        state["last_report"] = None
        st.rerun()


# ---------------------------------------------------------------------------
# Studio action dispatcher (handles cross-panel actions)
# ---------------------------------------------------------------------------


def handle_studio_action(st: Any, state: dict[str, Any]) -> None:
    """Check if a studio action was requested from another panel (e.g. chat)."""
    action = state.get("studio_action")
    if not action:
        return

    # Clear the action so it runs only once
    state["studio_action"] = None
    if "studio_action" in st.session_state:
        st.session_state.studio_action = None

    config_obj = state.get("config")
    client = state.get("client")
    last_report = state.get("last_report")
    if not last_report:
        st.warning("No report available. Run a swarm query first.")
        return

    if action == "diagram":
        _generate_diagram(st, config_obj, client, last_report)
    elif action == "narration":
        _generate_narration(st, config_obj, client, last_report)


# ---------------------------------------------------------------------------
# Quick-action implementations
# ---------------------------------------------------------------------------


def _generate_diagram(
    st: Any,
    config_obj: Any,
    client: Any,
    report: dict[str, Any] | None,
) -> None:
    """Generate a diagram image using Lemonade Flux and display it."""
    if not report or not config_obj or not client:
        st.warning("Lemonade client or report not available.")
        return

    prompt = (
        "Create a clean, professional diagram summarising this research report. "
        "Use a flowchart or mind-map style with clear labels:\n\n"
        f"Title: {report.get('title', '')}\n"
        f"Executive Summary: "
        f"{report.get('executive_summary', '')[:500]}\n"
        f"Sections: "
        f"{', '.join(s.get('heading', '') for s in report.get('sections', []))}\n"
        f"Conclusion: {report.get('conclusion', '')}"
    )

    with st.spinner("Generating diagram…"):
        try:
            result = asyncio.run(
                client.image_generation(
                    model=config_obj.models.image,
                    prompt=prompt,
                ),
            )
            data = result.get("data", [])
            if data and len(data) > 0:
                item = data[0]
                if "b64_json" in item:
                    img_bytes = base64.b64decode(item["b64_json"])
                    st.image(img_bytes, caption="Generated Diagram")
                elif "url" in item:
                    st.image(item["url"], caption="Generated Diagram")
                else:
                    st.json(result)
            else:
                st.json(result)
        except Exception as exc:
            st.error(f"Diagram generation failed: {exc}")


def _generate_narration(
    st: Any,
    config_obj: Any,
    client: Any,
    report: dict[str, Any] | None,
) -> None:
    """Generate TTS narration of the executive summary."""
    if not report or not config_obj or not client:
        st.warning("Lemonade client or report not available.")
        return

    summary = report.get("executive_summary", "")
    if not summary:
        st.warning("No executive summary to narrate.")
        return

    with st.spinner("Generating narration…"):
        try:
            audio_bytes = asyncio.run(
                client.text_to_speech(
                    model=config_obj.models.tts,
                    input_text=summary[:1000],
                    voice="default",
                ),
            )
            st.audio(audio_bytes, format="audio/wav")
        except Exception as exc:
            st.error(f"Narration failed: {exc}")


def _generate_summary(
    st: Any,
    config_obj: Any,
    client: Any,
    report: dict[str, Any] | None,
) -> None:
    """Generate a TL;DR summary using the LLM."""
    if not report or not config_obj or not client:
        st.warning("Lemonade client or report not available.")
        return

    prompt = (
        "Summarise the following research report into a concise TL;DR "
        "(2–3 sentences):\n\n"
        f"Title: {report.get('title', '')}\n"
        f"Executive Summary: {report.get('executive_summary', '')}\n"
        f"Conclusion: {report.get('conclusion', '')}"
    )

    with st.spinner("Generating summary…"):
        try:
            result = asyncio.run(
                client.chat_completion(
                    model=config_obj.models.conductor,
                    messages=[
                        {
                            "role": "system",
                            "content": "You provide concise TL;DR summaries.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    stream=False,
                ),
            )
            if isinstance(result, dict):
                content = (
                    result.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                st.info(f"**TL;DR**  \n{content}")
        except Exception as exc:
            st.error(f"Summary generation failed: {exc}")


def _save_current_report(
    st: Any,
    state: dict[str, Any],
    report: dict[str, Any] | None,
) -> None:
    """Save the current report to session-state notes."""
    if not report:
        return
    if "saved_notes" not in st.session_state:
        st.session_state.saved_notes = []

    st.session_state.saved_notes.append({
        "title": report.get("title", "Untitled Report")[:80],
        "content": _report_to_markdown(report),
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    state["saved_notes"] = st.session_state.saved_notes
    st.markdown(
        '<div class="amd-banner success"><span class="icon"><i class="ph-bold ph-check-circle"></i></span>'
        '<span>Report saved!</span></div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def _report_to_markdown(report: dict[str, Any]) -> str:
    """Convert a report dict to a plain Markdown string."""
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


def _report_to_html(report: dict[str, Any]) -> str:
    """Convert a report dict to a standalone HTML document."""
    md = _report_to_markdown(report)

    # Escape HTML special chars
    body = md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Process line-by-line for markdown -> HTML conversion
    lines: list[str] = []
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("###### "):
            lines.append(f"<h6>{stripped[7:]}</h6>")
        elif stripped.startswith("##### "):
            lines.append(f"<h5>{stripped[6:]}</h5>")
        elif stripped.startswith("#### "):
            lines.append(f"<h4>{stripped[5:]}</h4>")
        elif stripped.startswith("### "):
            lines.append(f"<h3>{stripped[4:]}</h3>")
        elif stripped.startswith("## "):
            lines.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith("# "):
            lines.append(f"<h1>{stripped[2:]}</h1>")
        elif stripped.startswith("- "):
            lines.append(f"<li>{stripped[2:]}</li>")
        elif stripped == "":
            lines.append("<br>")
        else:
            lines.append(line)

    body = "\n".join(lines)

    title = report.get("title", "SwarmMind Report")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 800px;
       margin: 0 auto; padding: 2em; line-height: 1.6; color: #1a1a2e; }}
h1, h2, h3 {{ color: #1a1a2e; }}
li {{ margin: 0.25em 0; }}
</style>
</head>
<body>
{body}
</body>
</html>"""