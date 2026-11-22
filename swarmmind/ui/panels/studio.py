"""Studio / action panel for the SwarmMind UI."""

from __future__ import annotations

import asyncio
import base64
import html
import re
from datetime import datetime
from typing import Any

from swarmmind.ui.components.icons import icon_html
from swarmmind.ui.panels.chat import (
    build_manifest_lines,
    build_sources_appendix,
    grounding_text,
    link_citations,
    source_cards_html,
)

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

    if not has_report:
        st.markdown(
            '<div class="action-hint">Actions unlock once you run a swarm query.</div>',
            unsafe_allow_html=True,
        )

    # Two compact columns keep the row readable in the narrow right panel.
    qa_left, qa_right = st.columns(2)
    with qa_left:
        if st.button(
            "Full Report",
            use_container_width=True,
            disabled=not has_report,
            key="full_report_btn",
        ):
            st.info("Report is displayed in the centre panel above.")
        if st.button(
            "Narration",
            use_container_width=True,
            disabled=not has_report,
            key="narration_btn",
        ):
            _generate_narration(st, config_obj, client, last_report)
    with qa_right:
        if st.button(
            "Diagram",
            use_container_width=True,
            disabled=not has_report,
            key="diagram_btn",
        ):
            _generate_diagram(st, config_obj, client, last_report)
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
        md_content = _report_to_markdown(last_report, config_obj)
        html_content = _report_to_html(last_report, config_obj)

        exp_md, exp_html = st.columns(2)
        with exp_md:
            st.download_button(
                "Markdown",
                data=md_content,
                file_name="swarmmind_report.md",
                mime="text/markdown",
                use_container_width=True,
                key="export_md",
            )
        with exp_html:
            st.download_button(
                "HTML",
                data=html_content,
                file_name="swarmmind_report.html",
                mime="text/html",
                use_container_width=True,
                key="export_html",
            )
    else:
        st.markdown(
            '<div class="action-hint">Exports unlock after your first swarm query.</div>',
            unsafe_allow_html=True,
        )

    st.divider()

    # ------------------------------------------------------------------
    # Lemonade Info
    # ------------------------------------------------------------------
    st.markdown(f"### {icon_html('link')} Lemonade", unsafe_allow_html=True)

    conn_status = state.get("connection_status", "unknown")
    conn_view = {
        "ok": ("conn-ok", "Connected"),
        "error": ("conn-err", "Disconnected"),
    }.get(conn_status, ("conn-unknown", "Unknown"))
    conn_css, conn_label = conn_view
    st.markdown(
        f'<span class="conn-state {conn_css}">'
        f'{icon_html("circle", "fill")} {conn_label}</span>',
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
            # WP5 contract: text_to_speech(text, model=None, voice="af") -> bytes
            result = asyncio.run(
                client.text_to_speech(
                    summary[:1000],
                    model=config_obj.models.tts,
                    voice="af",
                ),
            )
            audio_bytes = _audio_bytes(result)
            if not audio_bytes:
                st.warning("Narration returned no audio data.")
                return
            st.audio(audio_bytes, format="audio/wav")
        except Exception as exc:
            st.error(f"Narration failed: {exc}")


def _audio_bytes(result: Any) -> bytes:
    """Normalise TTS output (raw bytes, or a b64 dict from older clients)."""
    if isinstance(result, (bytes, bytearray)):
        return bytes(result)
    if isinstance(result, dict):
        encoded = result.get("b64_json") or result.get("audio")
        data = result.get("data")
        if not encoded and isinstance(data, list) and data:
            encoded = data[0].get("b64_json") if isinstance(data[0], dict) else None
        if encoded:
            try:
                return base64.b64decode(encoded)
            except Exception:
                return b""
    return b""


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
        "content": _report_to_markdown(report, state.get("config")),
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


def _report_to_markdown(report: dict[str, Any], config_obj: Any = None) -> str:
    """Convert a report dict to a Markdown string with citations and appendix."""
    lines: list[str] = [
        f"# {report.get('title', 'Research Report')}",
        "",
    ]

    summary = report.get("executive_summary", "")
    if summary:
        lines.append("## Executive Summary")
        lines.append(link_citations(summary))
        lines.append("")

    for section in report.get("sections", []):
        if not isinstance(section, dict):
            continue
        heading = section.get("heading", "")
        content = section.get("content", "")
        if heading:
            lines.append(f"## {link_citations(heading)}")
        if content:
            lines.append(link_citations(content))
        cites = section.get("sources") or []
        if cites:
            lines.append("**Cites:** " + " ".join(f"[{sid}](#source-{sid})" for sid in cites))
        lines.append("")

    conclusion = report.get("conclusion", "")
    if conclusion:
        lines.append("## Conclusion")
        lines.append(link_citations(conclusion))
        lines.append("")

    for c in report.get("contradictions", []):
        lines.append(f"- [!] {link_citations(c)}")
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
    lines.extend(build_manifest_lines(report, config_obj))

    return "\n".join(lines)


def _report_to_html(report: dict[str, Any], config_obj: Any = None) -> str:
    """Convert a report dict to a standalone HTML document with source cards."""
    md = _report_to_markdown(report, config_obj)

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
    # Make [S1]-style markers actual in-page links in the HTML export.
    body = re.sub(
        r"\[([^\]]+)\]\((#[^)\s]+)\)",
        r'<a href="\2" class="citation">[\1]</a>',
        body,
    )

    title = html.escape(str(report.get("title", "SwarmMind Report")))
    grounding = html.escape(grounding_text(report))
    cards = source_cards_html(report.get("sources") or [])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 800px;
       margin: 0 auto; padding: 2em; line-height: 1.6; color: #14213d;
       background: #fcfcff; }}
h1, h2, h3 {{ color: #14213d; }}
li {{ margin: 0.25em 0; }}
a.citation {{ color: #5b21b6; font-weight: 600; text-decoration: underline; }}
a.citation:focus-visible {{ outline: 2px solid #5b21b6; outline-offset: 2px; }}
.grounding-badge {{ display: inline-block; padding: .4em .8em; border-radius: 999px;
       background: #e0e7ff; color: #1e1b4b; border: 1px solid #a5b4fc; font-weight: 600; }}
.source-card {{ border: 1px solid #c7d2fe; border-left: 4px solid #6366f1;
       border-radius: 8px; padding: .75em 1em; margin: .6em 0; background: #fff; }}
.source-card:target {{ outline: 3px solid #6366f1; background: #eef2ff; }}
.source-card-head {{ font-size: .85em; color: #3730a3; display: flex; gap: .75em; }}
.source-title {{ font-weight: 700; }}
.source-url {{ font-family: monospace; font-size: .85em; color: #1d4ed8; word-break: break-all; }}
.source-snippet {{ margin: .5em 0 0; color: #374151; }}
.source-unverified {{ border-left-color: #d97706; }}
</style>
</head>
<body>
<div class="grounding-badge" role="status" aria-label="Grounding: {grounding}">{grounding}</div>
{body}
<h2>Sources</h2>
{cards}
</body>
</html>"""