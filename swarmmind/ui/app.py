"""SwarmMind Streamlit web UI — main entry point."""

from __future__ import annotations

import asyncio
from pathlib import Path

import streamlit as st

from swarmmind.config import Config
from swarmmind.core.hardware import (
    SystemHardware,
    describe_hardware,
    detect_hardware,
    has_amd_hardware,
    is_amd_optimized_target,
    recommend_backends,
)
from swarmmind.lemonade.client import LemonadeClient
from swarmmind.ui.components.icons import icon_html, load_phosphor_css
from swarmmind.ui.panels.chat import render_chat_panel
from swarmmind.ui.panels.sources import render_sources_panel
from swarmmind.ui.panels.studio import render_studio_panel

# ---------------------------------------------------------------------------
# Page configuration — must be the first Streamlit command
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="SwarmMind",
    page_icon="🐝",
    layout="wide",
    initial_sidebar_state="collapsed",
)

load_phosphor_css(st)

# ---------------------------------------------------------------------------
# Custom CSS — professional productivity-tool look
# ---------------------------------------------------------------------------

st.markdown(
    """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;600&display=swap');

    /* ========================================================
       Base & Typography
       ======================================================== */
    html, body, .stApp, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    }
    /* ---------- App-level background ---------- */
    .stApp > header { display: none !important; }
    .stApp { 
        background: radial-gradient(ellipse at top right, #160f3b 0%, #09090b 45%, #000000 100%) !important;
        color: #e2e8f0 !important;
        min-height: 100vh;
    }
    .main > div:first-child { padding-top: 0 !important; }
    .block-container { 
        padding-top: 1rem !important; 
        max-width: 100% !important;
        padding-left: 1.5rem !important;
        padding-right: 1.5rem !important;
    }

    /* ---------- Scrollbar ---------- */
    ::-webkit-scrollbar { width: 8px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.2); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.4); }

    /* ---------- App chrome / header bar ---------- */
    .app-chrome {
        background: rgba(15, 13, 28, 0.75);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border-radius: 16px;
        padding: 0.8rem 1.5rem;
        margin-bottom: 1.2rem;
        display: flex;
        align-items: center;
        gap: 1rem;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-bottom: 1px solid rgba(124, 58, 237, 0.3);
        position: relative;
        overflow: hidden;
    }
    .app-chrome::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 2px;
        background: linear-gradient(90deg, #7c3aed, #3b82f6, #06b6d4, #7c3aed);
        background-size: 200% 100%;
        animation: gradient-slide 4s linear infinite;
    }
    @keyframes gradient-slide {
        0% { background-position: 0% 50%; }
        100% { background-position: 200% 50%; }
    }
    .app-chrome:hover {
        box-shadow: 0 12px 40px rgba(0, 0, 0, 0.7);
        border-color: rgba(124, 58, 237, 0.4);
    }
    .app-chrome .logo-area {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        color: #ffffff;
        font-weight: 800;
        font-size: 1.35rem;
        letter-spacing: -0.01em;
        text-shadow: 0 2px 10px rgba(255, 255, 255, 0.15);
    }
    .app-chrome .logo-area .ph-fill { 
        color: #facc15; 
        filter: drop-shadow(0 0 10px rgba(250, 204, 21, 0.7)); 
        animation: bee-float 3s ease-in-out infinite;
    }
    @keyframes bee-float {
        0%, 100% { transform: translateY(0); }
        50% { transform: translateY(-3px); }
    }
    .app-chrome .chrome-status {
        margin-left: auto;
        display: flex;
        align-items: center;
        gap: 1rem;
        font-size: 0.85rem;
    }
    .app-chrome .chrome-status a {
        color: rgba(255,255,255,0.55);
        text-decoration: none;
        transition: color 0.2s, text-shadow 0.2s;
        font-weight: 500;
        font-size: 0.82rem;
    }
    .app-chrome .chrome-status a:hover { color: #fff; text-shadow: 0 0 8px rgba(255,255,255,0.4); }
    .app-chrome .badge {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        padding: 0.3rem 0.8rem;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        box-shadow: inset 0 1px 1px rgba(255,255,255,0.1);
    }
    .badge-ok {
        background: linear-gradient(135deg, rgba(16, 185, 129, 0.2), rgba(5, 150, 105, 0.4));
        color: #6ee7b7;
        border: 1px solid rgba(16, 185, 129, 0.4);
        box-shadow: 0 0 10px rgba(16, 185, 129, 0.2);
    }
    .badge-err {
        background: linear-gradient(135deg, rgba(239, 68, 68, 0.2), rgba(220, 38, 38, 0.4));
        color: #fca5a5;
        border: 1px solid rgba(239, 68, 68, 0.4);
        box-shadow: 0 0 10px rgba(239, 68, 68, 0.2);
    }

    /* ---------- Panel cards ---------- */
    div[data-testid="column"] > div:first-child > div:first-child {
        background: rgba(20, 20, 25, 0.5);
        backdrop-filter: blur(16px);
        -webkit-backdrop-filter: blur(16px);
        border: 1px solid rgba(255, 255, 255, 0.07);
        border-radius: 16px;
        padding: 1.25rem 1.5rem;
        box-shadow: 0 8px 32px rgba(0,0,0,0.4);
        min-height: 75vh;
        transition: transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275), box-shadow 0.4s ease, border-color 0.4s ease;
        position: relative;
    }
    div[data-testid="column"] > div:first-child > div:first-child:hover {
        transform: translateY(-6px) scale(1.01);
        box-shadow: 0 20px 48px rgba(0,0,0,0.7), 0 0 15px rgba(139, 92, 246, 0.3);
        border-color: rgba(139, 92, 246, 0.4);
    }
    
    /* Neon glowing left accent borders per column */
    div[data-testid="column"]:nth-child(1) > div:first-child > div:first-child {
        border-left: 4px solid #8b5cf6;
        box-shadow: -4px 0 20px rgba(139, 92, 246, 0.2), 0 8px 32px rgba(0,0,0,0.4);
    }
    div[data-testid="column"]:nth-child(2) > div:first-child > div:first-child {
        border-left: 4px solid #3b82f6;
        box-shadow: -4px 0 20px rgba(59, 130, 246, 0.2), 0 8px 32px rgba(0,0,0,0.4);
    }
    div[data-testid="column"]:nth-child(3) > div:first-child > div:first-child {
        border-left: 4px solid #f97316;
        box-shadow: -4px 0 20px rgba(249, 115, 22, 0.2), 0 8px 32px rgba(0,0,0,0.4);
    }

    /* Panel heading */
    div[data-testid="column"] h3 {
        font-size: 0.9rem !important;
        font-weight: 800 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.1em !important;
        color: #cbd5e1 !important;
        margin: 0 0 1.2rem 0 !important;
        padding-bottom: 0.8rem !important;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1) !important;
        display: flex;
        align-items: center;
        gap: 0.5rem;
        text-shadow: 0 2px 4px rgba(0,0,0,0.5);
    }
    div[data-testid="column"] h3 .ph {
        font-size: 1.2rem !important;
        color: #94a3b8;
    }

    /* AMD banner */
    .amd-banner {
        padding: 0.8rem 1rem;
        border-radius: 12px;
        margin-bottom: 1rem;
        font-size: 0.85rem;
        font-weight: 500;
        display: flex;
        align-items: center;
        gap: 0.75rem;
        line-height: 1.5;
        backdrop-filter: blur(8px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    }
    .amd-banner.success {
        background: rgba(16, 185, 129, 0.1);
        border: 1px solid rgba(16, 185, 129, 0.3);
        color: #6ee7b7;
    }
    .amd-banner.info {
        background: rgba(59, 130, 246, 0.1);
        border: 1px solid rgba(59, 130, 246, 0.3);
        color: #93c5fd;
    }
    .amd-banner.warning {
        background: rgba(245, 158, 11, 0.1);
        border: 1px solid rgba(245, 158, 11, 0.3);
        color: #fcd34d;
    }

    /* ---------- Streamlit widget overrides ---------- */
    div.stButton > button {
        border-radius: 10px !important;
        font-weight: 600 !important;
        font-size: 0.85rem !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        background: rgba(255,255,255,0.04) !important;
        color: #cbd5e1 !important;
        padding: 0.45rem 1.2rem !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
        box-shadow: 0 2px 4px rgba(0,0,0,0.25) !important;
        letter-spacing: 0.02em !important;
    }
    div.stButton > button:hover {
        border-color: rgba(255,255,255,0.25) !important;
        background: rgba(255,255,255,0.08) !important;
        box-shadow: 0 4px 16px rgba(0,0,0,0.4) !important;
        transform: translateY(-1px) !important;
        color: #ffffff !important;
    }
    div.stButton > button[kind="primary"],
    div.stButton > button[data-testid="baseButton-primary"] {
        background: linear-gradient(135deg, #4338ca 0%, #7c3aed 50%, #9333ea 100%) !important;
        color: #ffffff !important;
        border: none !important;
        box-shadow: 0 4px 20px rgba(124, 58, 237, 0.5), inset 0 1px 0 rgba(255,255,255,0.15) !important;
        text-shadow: 0 1px 2px rgba(0,0,0,0.3) !important;
    }
    div.stButton > button[kind="primary"]:hover,
    div.stButton > button[data-testid="baseButton-primary"]:hover {
        box-shadow: 0 8px 30px rgba(124, 58, 237, 0.7), inset 0 1px 0 rgba(255,255,255,0.2) !important;
        transform: translateY(-2px) !important;
        filter: brightness(1.08);
    }
    /* Launch Swarm — special treatment */
    div.stButton > button[kind="primary"]:not([disabled]) {
        animation: pulse-glow 3s ease-in-out infinite;
    }
    @keyframes pulse-glow {
        0%, 100% { box-shadow: 0 4px 20px rgba(124,58,237,0.5), inset 0 1px 0 rgba(255,255,255,0.15); }
        50% { box-shadow: 0 6px 35px rgba(124,58,237,0.75), 0 0 60px rgba(124,58,237,0.2), inset 0 1px 0 rgba(255,255,255,0.2); }
    }

    /* Fix for text inputs and textareas */
    label, .stTextInput label, .stTextArea label,
    div[data-testid="stWidgetLabel"] p, .stSlider label,
    div[data-testid="stWidgetLabel"] {
        font-size: 0.78rem !important;
        font-weight: 600 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.07em !important;
        color: #94a3b8 !important;
        margin-bottom: 0.3rem !important;
    }
    .stTextInput input,
    .stTextArea textarea {
        border-radius: 10px !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        padding: 0.6rem 0.8rem !important;
        font-size: 0.9rem !important;
        transition: all 0.2s ease !important;
        background: rgba(5, 5, 15, 0.7) !important;
        color: #f8fafc !important;
        box-shadow: inset 0 2px 4px rgba(0,0,0,0.3), 0 0 0 0px rgba(139,92,246,0) !important;
        caret-color: #a78bfa !important;
    }
    .stTextInput input:focus,
    .stTextArea textarea:focus {
        border-color: rgba(139, 92, 246, 0.7) !important;
        box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.2), inset 0 2px 4px rgba(0,0,0,0.3) !important;
        background: rgba(5, 5, 20, 0.85) !important;
        outline: none !important;
    }

    div[data-baseweb="select"] > div {
        border-radius: 10px !important;
        border: 1px solid rgba(255,255,255,0.1) !important;
        background: rgba(0, 0, 0, 0.3) !important;
        color: #f8fafc !important;
    }
    div[data-baseweb="select"] > div:hover {
        border-color: rgba(255,255,255,0.3) !important;
    }

    .streamlit-expanderHeader {
        border-radius: 10px !important;
        font-size: 0.85rem !important;
        font-weight: 600 !important;
        padding: 0.6rem 0.8rem !important;
        background: rgba(255,255,255,0.03) !important;
        border: 1px solid rgba(255,255,255,0.05) !important;
        color: #e2e8f0 !important;
        transition: all 0.2s ease;
    }
    .streamlit-expanderHeader:hover { 
        background: rgba(255,255,255,0.08) !important; 
        border-color: rgba(255,255,255,0.15) !important;
    }
    .streamlit-expanderContent {
        border-left: 2px solid rgba(139, 92, 246, 0.5) !important;
        margin-left: 0.5rem !important;
        padding-left: 0.75rem !important;
        margin-top: 0.5rem !important;
        color: #cbd5e1 !important;
    }

    .stCheckbox > label {
        font-size: 0.85rem !important;
        gap: 0.5rem !important;
        color: #cbd5e1 !important;
    }

    div[data-testid="stSlider"] > div {
        padding-top: 0.25rem !important;
    }

    .status-dot {
        display: inline-block;
        width: 8px; height: 8px;
        border-radius: 50%;
        margin-right: 6px;
        box-shadow: 0 0 8px currentColor;
    }
    .status-dot.ok { background: #4ade80; color: #4ade80; }
    .status-dot.error { background: #f87171; color: #f87171; }

    div[data-testid="stMetric"] {
        background: rgba(0, 0, 0, 0.2);
        border: 1px solid rgba(255,255,255,0.05);
        border-radius: 12px;
        padding: 0.75rem 1rem;
        box-shadow: inset 0 2px 4px rgba(0,0,0,0.1);
        transition: transform 0.2s, box-shadow 0.2s;
    }
    div[data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.3), inset 0 2px 4px rgba(0,0,0,0.1);
        border-color: rgba(255,255,255,0.1);
    }
    div[data-testid="stMetric"] > div:first-child {
        font-size: 0.75rem !important;
        color: #94a3b8 !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    div[data-testid="stMetric"] > div:nth-child(2) {
        font-size: 1.25rem !important;
        font-weight: 800 !important;
        color: #f8fafc !important;
        text-shadow: 0 2px 4px rgba(0,0,0,0.5);
    }

    hr {
        margin: 0.75rem 0 !important;
        border-color: rgba(255,255,255,0.1) !important;
    }

    div.stAlert {
        border-radius: 12px !important;
        border-left-width: 4px !important;
        font-size: 0.85rem !important;
        padding: 0.6rem 1rem !important;
    }

    div[data-testid="column"] { gap: 0 !important; }

    /* ---- FORCE dark inputs — every Streamlit version ---- */
    textarea, .stTextArea textarea,
    input[type="text"], input[type="number"], input[type="email"],
    .stTextInput input, .stNumberInput input {
        background-color: rgba(10, 10, 20, 0.6) !important;
        background: rgba(10, 10, 20, 0.6) !important;
        color: #f8fafc !important;
        border: 1px solid rgba(255,255,255,0.12) !important;
        border-radius: 10px !important;
    }
    textarea:focus, .stTextArea textarea:focus,
    input[type="text"]:focus, .stTextInput input:focus {
        border-color: #8b5cf6 !important;
        box-shadow: 0 0 0 3px rgba(139,92,246,0.25) !important;
        outline: none !important;
    }
    /* Streamlit wraps inputs in multiple divs — target them all */
    [data-baseweb="textarea"] textarea,
    [data-baseweb="input"] input {
        background: rgba(10, 10, 20, 0.6) !important;
        color: #f8fafc !important;
    }
    /* Selectbox / multiselect dropdown */
    [data-baseweb="select"] > div {
        background: rgba(10,10,20,0.6) !important;
        border-color: rgba(255,255,255,0.12) !important;
        color: #f8fafc !important;
    }

    /* ---- Hide Streamlit top-right toolbar / deploy button ---- */
    #MainMenu, header[data-testid="stHeader"] { display: none !important; }
    .stDeployButton { display: none !important; }

    /* ---- Expander styling ---- */
    div[data-testid="stExpander"] {
        border: 1px solid rgba(255,255,255,0.08) !important;
        border-radius: 10px !important;
        background: rgba(0,0,0,0.2) !important;
        margin-bottom: 0.5rem;
    }
    div[data-testid="stExpander"] summary {
        font-size: 0.85rem !important;
        font-weight: 600 !important;
        color: #cbd5e1 !important;
    }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Load configuration
# ---------------------------------------------------------------------------


# Load configuration — re-read on every render so config.toml changes
# (e.g. from the demo capture script) take immediate effect.
def _load_config() -> Config:
    return Config()  # type: ignore[call-arg]


def _get_client(config: Config) -> LemonadeClient:
    return LemonadeClient(config.get_lemonade_base_url())


_config = _load_config()
_client = _get_client(_config)

# ---------------------------------------------------------------------------
# Initialise session state
# ---------------------------------------------------------------------------

if "config" not in st.session_state:
    st.session_state.config = _config

if "conversation_history" not in st.session_state:
    st.session_state.conversation_history = []

if "current_project" not in st.session_state:
    st.session_state.current_project = None

if "last_report" not in st.session_state:
    st.session_state.last_report = None

if "saved_notes" not in st.session_state:
    st.session_state.saved_notes = []

if "studio_action" not in st.session_state:
    st.session_state.studio_action = None

if "hardware" not in st.session_state:
    st.session_state.hardware = None

if "backend_recommendations" not in st.session_state:
    st.session_state.backend_recommendations = {}

# ---------------------------------------------------------------------------
# Initialise database
# ---------------------------------------------------------------------------

if "db" not in st.session_state:
    from swarmmind.data.database import Database  # noqa: PLC0415
    from swarmmind.core.async_utils import run_async

    db_path = str(Path.home() / ".swarmmind" / "swarmmind.db")
    db = Database(db_path)
    try:
        run_async(db.connect())
        run_async(db.init_db())
        st.session_state.db = db
    except Exception:
        st.session_state.db = None

# ---------------------------------------------------------------------------
# Initialise ChromaStore
# ---------------------------------------------------------------------------

if "chroma" not in st.session_state:
    from swarmmind.rag.chroma_store import ChromaStore  # noqa: PLC0415

    chroma_dir = str(Path.home() / ".swarmmind" / "chroma_db")
    try:
        st.session_state.chroma = ChromaStore(chroma_dir)
    except Exception:
        st.session_state.chroma = None

# ---------------------------------------------------------------------------
# Connection check
# ---------------------------------------------------------------------------


async def _check_health() -> str:
    result = await _client.health_check()
    return result.get("status", "error")


async def _detect_hardware_async() -> SystemHardware | None:
    try:
        return await detect_hardware(_client)
    except Exception:
        return None


# Always re-check connection — re-read config so demo config.toml is honoured
from swarmmind.core.async_utils import run_async  # noqa: PLC0415
try:
    _status = run_async(_check_health())
    st.session_state.connection_status = _status
except Exception as exc:
    import sys
    print(f"Health check failed with exception: {exc}", file=sys.stderr)
    st.session_state.connection_status = "error"

conn_status: str = st.session_state.connection_status
is_ok = conn_status == "ok"
dot_class = "ok" if is_ok else "error"

# ---------------------------------------------------------------------------
# App chrome — dark header bar
# ---------------------------------------------------------------------------

st.markdown(
    f"""
<div class="app-chrome">
    <div class="logo-area">
        {icon_html("bee", "fill", "1.3rem")}
        <span>SwarmMind</span>
    </div>
    <div class="chrome-status">
        <a href="#" onclick="return false;">Docs</a>
        <a href="#" onclick="return false;">GitHub</a>
        {"&nbsp;"}
        <span class="badge badge-{'ok' if is_ok else 'err'}">
            <span class="status-dot {'ok' if is_ok else 'error'}"></span>
            {conn_status}
        </span>
    </div>
</div>
""",
    unsafe_allow_html=True,
)

if not is_ok:
    st.markdown(
        '<div class="amd-banner warning">'
        f'{icon_html("warning-octagon", "bold")}'
        "<span>Cannot reach AMD Lemonade. Make sure the Lemonade server is running.</span>"
        "</div>",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Hardware detection + AMD optimisation banner
# ---------------------------------------------------------------------------

if st.session_state.hardware is None and is_ok:
    with st.spinner("Probing AMD hardware…"):
        from swarmmind.core.async_utils import run_async
        try:
            detected = run_async(_detect_hardware_async())
        except Exception as exc:
            import sys
            print(f"Hardware detect failed: {exc}", file=sys.stderr)
            detected = None
        st.session_state.hardware = detected
        if detected is not None:
            st.session_state.backend_recommendations = recommend_backends(detected)

hardware: SystemHardware | None = st.session_state.hardware

if hardware is not None and (
    has_amd_hardware(hardware) or is_amd_optimized_target(hardware)
):
    desc = describe_hardware(hardware)
    if is_amd_optimized_target(hardware):
        b_class = "success"
        label = "AMD Optimized"
        icon_name = "rocket"
    else:
        b_class = "info"
        label = "AMD Detected"
        icon_name = "info"
    st.markdown(
        f'<div class="amd-banner {b_class}">'
        f'{icon_html(icon_name, "fill")}'
        f"<span><strong>{label}</strong> &mdash; {desc}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
elif hardware is not None:
    st.markdown(
        '<div class="amd-banner warning">'
        f'{icon_html("warning-octagon", "bold")}'
        f"<span><strong>Generic Hardware</strong> &mdash; {describe_hardware(hardware)}</span>"
        "</div>",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Build shared state dict
# ---------------------------------------------------------------------------

_state: dict[str, object] = {
    "config": _config,
    "client": _client,
    "db": st.session_state.get("db"),
    "chroma": st.session_state.get("chroma"),
    "current_project": st.session_state.current_project,
    "conversation_history": st.session_state.conversation_history,
    "connection_status": conn_status,
    "last_report": st.session_state.last_report,
    "saved_notes": st.session_state.saved_notes,
    "studio_action": st.session_state.studio_action,
    "hardware": hardware,
    "backend_recommendations": st.session_state.backend_recommendations,
}

# ---------------------------------------------------------------------------
# 3-column layout
# ---------------------------------------------------------------------------

left_col, middle_col, right_col = st.columns([1.2, 3.6, 1.2], gap="large")

with left_col:
    render_sources_panel(st, _state)

with middle_col:
    render_chat_panel(st, _state)

with right_col:
    render_studio_panel(st, _state)
