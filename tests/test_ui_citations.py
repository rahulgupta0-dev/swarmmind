"""WP3 — UI citation rendering smoke tests with a fake Streamlit module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake Streamlit module
# ---------------------------------------------------------------------------


class _SessionState(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


class FakeSt:
    """Record calls made by panel renderers; no real Streamlit required."""

    def __init__(self) -> None:
        self.session_state: _SessionState = _SessionState()
        self.markdowns: list[str] = []
        self.audios: list[Any] = []
        self.buttons: dict[str, bool] = {}
        self.reruns = 0

    # -- context managers ---------------------------------------------------
    def __enter__(self) -> "FakeSt":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False

    def columns(self, spec: Any, **kwargs: Any) -> list["FakeSt"]:
        count = spec if isinstance(spec, int) else len(spec)
        return [FakeSt() for _ in range(count)]

    def expander(self, *args: Any, **kwargs: Any) -> "FakeSt":
        return FakeSt()

    def status(self, *args: Any, **kwargs: Any) -> "FakeSt":
        return FakeSt()

    def spinner(self, *args: Any, **kwargs: Any) -> "FakeSt":
        return FakeSt()

    def empty(self) -> "FakeSt":
        return FakeSt()

    # -- output -------------------------------------------------------------
    def markdown(self, body: str, **kwargs: Any) -> None:
        self.markdowns.append(str(body))

    def caption(self, body: str, **kwargs: Any) -> None:
        self.markdowns.append(str(body))

    def warning(self, body: str, **kwargs: Any) -> None:
        self.markdowns.append(str(body))

    def info(self, body: str, **kwargs: Any) -> None:
        self.markdowns.append(str(body))

    def error(self, body: str, **kwargs: Any) -> None:
        self.markdowns.append(str(body))

    def code(self, *args: Any, **kwargs: Any) -> None:
        pass

    def json(self, *args: Any, **kwargs: Any) -> None:
        pass

    def image(self, *args: Any, **kwargs: Any) -> None:
        pass

    def audio(self, data: Any, **kwargs: Any) -> None:
        self.audios.append(data)

    def divider(self) -> None:
        pass

    # -- widgets ------------------------------------------------------------
    def button(self, label: str, **kwargs: Any) -> bool:
        return bool(self.buttons.get(kwargs.get("key", label), False))

    def download_button(self, *args: Any, **kwargs: Any) -> None:
        pass

    def text_area(self, *args: Any, **kwargs: Any) -> str:
        return ""

    def text_input(self, *args: Any, **kwargs: Any) -> str:
        return ""

    def file_uploader(self, *args: Any, **kwargs: Any) -> None:
        return None

    def checkbox(self, *args: Any, **kwargs: Any) -> bool:
        return bool(kwargs.get("value", False))

    def multiselect(self, *args: Any, **kwargs: Any) -> list[str]:
        return list(kwargs.get("default", []))

    def selectbox(self, *args: Any, **kwargs: Any) -> str:
        options = kwargs.get("options") or []
        return str(options[0]) if options else ""

    def slider(self, *args: Any, **kwargs: Any) -> int:
        return int(kwargs.get("value", 1))

    def radio(self, *args: Any, **kwargs: Any) -> str:
        options = kwargs.get("options") or []
        return str(options[0]) if options else ""

    def rerun(self) -> None:
        self.reruns += 1


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _source() -> dict[str, Any]:
    return {
        "id": "S1", "kind": "web", "title": "AMD ROCm docs",
        "url": "https://example.com/rocm", "snippet": "ROCm is an open GPU stack.",
        "doc_id": "", "chunk": -1,
    }


def _report() -> dict[str, Any]:
    return {
        "title": "Test Report",
        "executive_summary": "ROCm is open [S1]. Unknown [S9].",
        "sections": [{
            "heading": "Findings [S1]",
            "content": "Exact evidence [S1] and bogus [S9].",
            "sources": ["S1"],
        }],
        "conclusion": "Done [S1]",
        "contradictions": [],
        "follow_up_questions": ["Why?"],
        "claims": [{
            "text": "Findings", "source_ids": ["S1"], "status": "supported",
        }],
        "sources": [_source()],
        "grounding": {
            "total_claims": 1, "supported": 1, "unverified": 0,
            "contradicted": 0, "citation_coverage": 1.0,
        },
        "stop_reason": "single_round",
        "rounds_used": 1,
        "timestamp": "2026-11-22T00:00:00+00:00",
        "query": "What is ROCm?",
    }


# ---------------------------------------------------------------------------
# Import smoke
# ---------------------------------------------------------------------------


def test_panels_import_cleanly() -> None:
    from swarmmind.ui.panels.chat import render_chat_panel  # noqa: F401
    from swarmmind.ui.panels.sources import render_sources_panel  # noqa: F401
    from swarmmind.ui.panels.studio import render_studio_panel  # noqa: F401


# ---------------------------------------------------------------------------
# Citation + card helpers
# ---------------------------------------------------------------------------


def test_link_citations_makes_accessible_anchors() -> None:
    from swarmmind.ui.panels.chat import link_citations

    assert link_citations("See [S1] and [S12].") == (
        "See [S1](#source-S1) and [S12](#source-S12)."
    )
    assert link_citations(None) == ""


def test_source_cards_include_anchor_title_kind_url_snippet() -> None:
    from swarmmind.ui.panels.chat import source_cards_html

    cards = source_cards_html([_source()])
    assert 'id="source-S1"' in cards
    assert 'tabindex="0"' in cards
    assert "AMD ROCm docs" in cards
    assert "web" in cards
    assert "https://example.com/rocm" in cards
    assert "ROCm is an open GPU stack." in cards
    assert "No sources were retrieved" in source_cards_html([])


def test_source_cards_mark_model_knowledge_unverified() -> None:
    from swarmmind.ui.panels.chat import source_cards_html

    cards = source_cards_html([{"id": "S2", "kind": "model", "title": "LLM memory"}])
    assert "source-unverified" in cards
    assert "model knowledge — unverified" in cards


# ---------------------------------------------------------------------------
# Report rendering with a fake st
# ---------------------------------------------------------------------------


def test_render_report_has_badge_clickable_citations_and_cards() -> None:
    from swarmmind.ui.panels.chat import _render_report

    st = FakeSt()
    _render_report(st, _report())
    joined = "\n".join(st.markdowns)
    assert 'role="status"' in joined
    assert "1/1 claims supported" in joined
    assert "[S1](#source-S1)" in joined
    assert 'id="source-S1"' in joined
    assert "Run manifest" in joined
    assert "Re-run this query" not in joined  # button, not markdown


def test_render_report_with_zero_sources_still_renders_badge() -> None:
    from swarmmind.ui.panels.chat import _render_report

    report = _report()
    report.update({
        "sources": [], "claims": [], "grounding": {"total_claims": 0},
        "sections": [], "executive_summary": "Model knowledge only.",
    })
    st = FakeSt()
    _render_report(st, report)
    joined = "\n".join(st.markdowns)
    assert "grounding-badge" in joined
    assert "No sources were retrieved" in joined


def test_source_status_refresh_is_bounded() -> None:
    from swarmmind.ui.panels.sources import _inject_status_refresh

    st = FakeSt()
    _inject_status_refresh(st)
    assert "window.location.reload()" in "\n".join(st.markdowns)
    for _ in range(25):
        _inject_status_refresh(st)
    assert "taking longer than expected" in "\n".join(st.markdowns)


def test_grounded_empty_state_and_chat_smoke() -> None:
    from swarmmind.ui.panels.chat import render_chat_panel

    st = FakeSt()
    state: dict[str, Any] = {
        "config": None, "client": None, "db": None, "chroma": None,
        "current_project": None, "conversation_history": [],
        "last_report": None, "connection_status": "ok", "hardware": None,
        "saved_notes": [], "studio_action": None,
    }
    render_chat_panel(st, state)
    joined = "\n".join(st.markdowns)
    assert "Add a PDF, URL, or note to ground your research" in joined

    # With a report present, rendering must remain exception-free.
    st2 = FakeSt()
    state["last_report"] = _report()
    render_chat_panel(st2, state)
    assert any("Test Report" in m for m in st2.markdowns)


# ---------------------------------------------------------------------------
# Export helpers (Studio + CLI)
# ---------------------------------------------------------------------------


def test_markdown_and_html_exports_include_appendix_and_manifest() -> None:
    from swarmmind.ui.panels.studio import _report_to_html, _report_to_markdown

    md = _report_to_markdown(_report())
    assert "## Sources" in md and "[S1]" in md and "AMD ROCm docs" in md
    assert "## Run Manifest" in md and "single_round" in md
    assert "1/1 claims supported" in md

    html = _report_to_html(_report())
    assert 'href="#source-S1"' in html
    assert 'id="source-S1"' in html
    assert "single_round" in html


def test_cli_report_helpers() -> None:
    from swarmmind.cli.main import _grounding_line, _manifest_lines, _sources_markdown

    report = _report()
    assert "1/1 claims supported" in _grounding_line(report)
    appendix = "\n".join(_sources_markdown(report))
    assert "[S1]" in appendix and "AMD ROCm docs" in appendix
    assert "single_round" in "\n".join(_manifest_lines(report))


# ---------------------------------------------------------------------------
# Studio narration — new WP5 text_to_speech contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("audio_payload", [b"RIFFfake-wav", {"b64_json": "UklGRg=="}])
def test_narration_uses_new_tts_signature(audio_payload: Any) -> None:
    from swarmmind.ui.panels.studio import _generate_narration

    calls: list[dict[str, Any]] = []

    class TtsClient:
        async def text_to_speech(self, text: str, model: str | None = None, voice: str = "af") -> bytes:
            calls.append({"text": text, "model": model, "voice": voice})
            return audio_payload  # type: ignore[return-value]

    cfg = SimpleNamespace(models=SimpleNamespace(tts="kokoro-v1"))
    st = FakeSt()
    _generate_narration(st, cfg, TtsClient(), _report())
    assert calls and calls[0]["text"] == _report()["executive_summary"][:1000]
    assert calls[0]["model"] == "kokoro-v1"
    assert calls[0]["voice"] == "af"
    assert st.audios and isinstance(st.audios[0], bytes)
