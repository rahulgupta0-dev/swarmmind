#!/usr/bin/env python3
"""SwarmMind demo capture — starts mock Lemonade + Streamlit, drives Playwright,
captures screenshots, and compiles frames into MP4.
"""
from __future__ import annotations

import http.server
import json
import os
import signal
import socket
import socketserver
import subprocess
import sys
import time
import urllib.request
from pathlib import Path



# ---------------------------------------------------------------------------
# Paths & ports
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
MOCK_PORT = 13305
STREAMLIT_PORT = 8501
OUTPUT_DIR = PROJECT_DIR / "demo_output"
SCREENSHOT_DIR = OUTPUT_DIR / "screenshots"
FRAME_DIR = SCREENSHOT_DIR / "frames"
VIDEO_DIR = OUTPUT_DIR / "video"

# ---------------------------------------------------------------------------
# Mock data / responses
# ---------------------------------------------------------------------------
MOCK_CONDUCTOR_TASKS = [
    {
        "worker_type": "web",
        "task": "Search for latest AMD Ryzen AI NPU specifications, TOPS ratings, and supported models",
        "context": "Focus on XDNA 2 architecture, Ryzen AI 300 series, and Strix Halo.",
        "reasoning": "NPU specifications are rapidly evolving; web search provides the most current data.",
    },
    {
        "worker_type": "rag",
        "task": "Retrieve internal documents about AMD ROCm software stack and GPU compute capabilities",
        "context": "Look for ROCm 6.x release notes, supported GPU list, and known limitations.",
        "reasoning": "Local knowledge base may have detailed ROCm documentation that web search might miss.",
    },
    {
        "worker_type": "analysis",
        "task": "Analyse the competitive landscape of AMD AI accelerators",
        "context": "Focus on ROCm ecosystem maturity, developer tools, framework support, and performance.",
        "reasoning": "Leverage LLM knowledge for architectural comparison and market analysis.",
    },
]

MOCK_WORKER_FINDINGS = (
    "# Key Findings\n\n"
    "## AMD Ryzen AI NPU Architecture\n"
    "The AMD Ryzen AI NPU (XDNA 2) represents a significant advancement in on-device AI acceleration. "
    "Built on a dataflow architecture with 32 AI engine tiles, it delivers up to 50 TOPS of INT8 performance "
    "while maintaining exceptional power efficiency.\n\n"
    "## ROCm Software Stack\n"
    "AMD's ROCm 6.x has matured significantly with expanded GPU support including RDNA 3 and CDNA 3 architectures. "
    "Key improvements include enhanced HIP runtime, improved LLM inference performance through Composable Kernel "
    "optimizations, and expanded framework support including PyTorch 2.x, TensorFlow, and JAX.\n\n"
    "## Ecosystem Growth\n"
    "AMD's open-source approach offers compelling advantages including no vendor lock-in, "
    "competitive raw compute performance, and a rapidly expanding community of developers building on the ROCm platform."
)

MOCK_SYNTHESIS_REPORT = {
    "title": "AMD AI Accelerator Ecosystem: Latest Advances",
    "executive_summary": (
        "AMD has made substantial strides in the AI accelerator space with three complementary platforms: "
        "the Ryzen AI NPU (XDNA 2) for efficient on-device inference, Radeon GPUs with ROCm for compute-intensive "
        "workloads, and the unified Ryzen AI Max+ processor family that combines all three. "
        "This analysis synthesises findings from web research, internal RAG retrieval, and competitive landscape analysis "
        "across the AMD AI ecosystem as of mid-2025, covering hardware specifications, software stack maturity, "
        "developer tooling, and benchmark performance relative to competing platforms."
    ),
    "sections": [
        {
            "heading": "Ryzen AI NPU (XDNA 2) Architecture",
            "content": (
                "The AMD Ryzen AI NPU (XDNA 2) represents a significant advancement in on-device AI acceleration. "
                "Built on a dataflow architecture with 32 AI engine tiles, it delivers up to 50 TOPS of INT8 performance "
                "while maintaining exceptional power efficiency below 5W in burst mode.\n\n"
                "Key architectural highlights include: dedicated SRAM per tile for low-latency data access, "
                "a shared DRAM controller for large model weights, hardware-level quantization support (INT4/INT8/FP16), "
                "and tight integration with the Ryzen AI Software stack enabling zero-copy model deployment from system memory. "
                "The XDNA 2 architecture specifically targets Transformer-based workloads with optimized attention "
                "computation units, making it particularly well-suited for local LLM inference tasks under 7B parameters."
            ),
        },
        {
            "heading": "ROCm 6.x Software Ecosystem",
            "content": (
                "AMD's ROCm 6.x has matured significantly with expanded GPU support including RDNA 3 and CDNA 3 architectures. "
                "Key improvements include enhanced HIP runtime, improved LLM inference performance through Composable Kernel "
                "optimizations, and expanded framework support "
                "including PyTorch 2.x with torch.compile support, TensorFlow, and JAX.\n\n"
                "The ROCm software stack now ships with pre-built Docker containers for popular frameworks, "
                "significantly reducing setup friction. Flash Attention 2 is natively supported via the "
                "rocmSoftwarePlatform/flash-attention fork, with FP8 attention variants landing in ROCm 6.2. "
                "Llama.cpp ROCm backend benchmarks show exceptional token generation rates for Llama-3.1-8B on the RX 7900 XTX, "
                "providing state-of-the-art performance for local inference at a highly accessible price point."
            ),
        },
        {
            "heading": "Strix Halo (Ryzen AI Max+): Unified Platform",
            "content": (
                "The Ryzen AI Max+ 395 (codenamed Strix Halo) represents AMD's most ambitious integration: "
                "a CPU (Zen 5, 16-core), integrated Radeon 890M GPU (40 RDNA 3.5 CUs), and the full XDNA 2 NPU "
                "on a single die with a 256-bit wide LPDDR5X memory interface providing up to 256 GB/s bandwidth.\n\n"
                "This unified memory architecture allows the GPU, NPU, and CPU to share the same physical memory pool "
                "without costly PCIe copy overhead — a significant advantage for local AI workloads that mix "
                "prefill (GPU-heavy) and decode (memory-bandwidth-heavy) phases. Early benchmarks show the "
                "Radeon 890M matching discrete RX 7600 performance in LLM inference while consuming under 35W TDP."
            ),
        },
        {
            "heading": "Open Source Advantage",
            "content": (
                "AMD's open-source approach via ROCm offers compelling advantages: no vendor lock-in, competitive raw "
                "compute performance on RDNA 3 hardware, and a rapidly growing community contribution ecosystem.\n\n"
                "The performance of pure LLM inference workloads using llama.cpp or vLLM with ROCm backends "
                "continues to break records. Furthermore, the NPU differentiation gives AMD a unique advantage in the "
                "Windows AI PC market where there is massive demand for efficient, local AI computation."
            ),
        },
    ],
    "conclusion": (
        "The AMD Ryzen AI Max+ 395 (Strix Halo) with integrated Radeon 8060S graphics and XDNA 2 NPU creates a "
        "compelling unified platform for local AI workloads."
    ),
    "contradictions": [
        "Some historical documentation for ROCm is still being updated to reflect the massive recent changes in 6.x",
    ],
    "follow_up_questions": [
        "What are the best open-source AMD resources for AI projects?",
        "How does AMD's Strix Halo optimize local LLM inference?",
        "Can the NPU be used independently for inference while the GPU handles training?",
    ],
}

MOCK_CHAT_RESPONSES = {
    "conductor": json.dumps(MOCK_CONDUCTOR_TASKS),
    "synthesis": json.dumps(MOCK_SYNTHESIS_REPORT),
    "worker": MOCK_WORKER_FINDINGS,
    "default": "I'm ready to help with your AMD AI research. What would you like to explore?",
}

# ---------------------------------------------------------------------------
# Mock server
# ---------------------------------------------------------------------------
class DemoHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/health":
            return self._json(200, {"status": "ok"})
        if self.path == "/v1/models":
            return self._json(
                200,
                {
                    "models": [
                        {"id": "Qwen3-6B", "backend": "cpu"},
                        {"id": "Qwen3-35B-A3B", "backend": "npu", "quant": "AWQ"},
                        {"id": "Phi-3.5-mini-instruct", "backend": "gpu"},
                    ]
                },
            )
        if self.path == "/v1/system-info":
            return self._json(
                200,
                {
                    "os": {"name": "Linux", "version": "6.8.0"},
                    "cpu": {"name": "AMD Ryzen 9 7945HX", "cores": 16, "arch": "x86_64"},
                    "gpus": [{"name": "AMD Radeon RX 7900 XTX", "vendor": "AMD", "vram_gb": 24.0, "backend": "rocm"}],
                    "npus": [{"name": "AMD XDNA 2 NPU", "vendor": "AMD", "backend": "ryzenai", "available": True}],
                    "memory": {"total_gb": 64.0, "available_gb": 48.0},
                },
            )
        if self.path == "/v1/system-stats":
            return self._json(
                200,
                {"cpu_percent": 12.5, "memory_percent": 25.0, "memory_available_gb": 48.0},
            )
        if self.path == "/v1/stats":
            return self._json(
                200,
                {
                    "ttft_ms": 45.0,
                    "tokens_per_second": 85.3,
                    "total_requests": 42,
                    "model_stats": {
                        "Qwen3-6B": {"requests": 10, "ttft_ms": 35, "tps": 92.1},
                        "Phi-3.5-mini-instruct": {"requests": 32, "ttft_ms": 48, "tps": 81.7},
                    },
                },
            )
        if self.path.endswith(".css") or self.path.endswith(".woff2"):
            return self._json(404, {"error": "not_found", "path": self.path})
        return self._json(404, {"error": "not_found", "path": self.path})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        data = json.loads(body) if body else {}

        if self.path == "/v1/chat/completions":
            messages = data.get("messages", [])
            raw_content = MOCK_CHAT_RESPONSES["default"]
            for msg in messages:
                content = msg.get("content", "") or ""
                if "conductor" in content.lower() or "decompose" in content.lower():
                    raw_content = MOCK_CHAT_RESPONSES["conductor"]
                    time.sleep(12.0)
                    break
                if "synthesis" in content.lower() or "merge" in content.lower():
                    raw_content = MOCK_CHAT_RESPONSES["synthesis"]
                    time.sleep(12.0)
                    break
                if "research" in content.lower() or "assistant" in content.lower():
                    raw_content = MOCK_CHAT_RESPONSES["worker"]
                    if "rag" in content.lower() or "retrieve" in content.lower():
                        time.sleep(15.0)
                    elif "code" in content.lower() or "generate" in content.lower():
                        time.sleep(18.0)
                    else:
                        time.sleep(16.0)
                    break
            return self._json(
                200,
                {
                    "choices": [{"message": {"role": "assistant", "content": raw_content}}],
                    "usage": {"prompt_tokens": 42, "completion_tokens": 128, "total_tokens": 170},
                },
            )
        if self.path == "/v1/load":
            return self._json(200, {"status": "loaded"})
        if self.path == "/v1/embeddings":
            return self._json(200, {"data": [{"embedding": [0.1] * 384, "index": 0}]})
        return self._json(404, {"error": "not_found", "path": self.path})

    def _json(self, code, obj):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        sys.stderr.write("[mock] " + fmt % args + "\n")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _wait_for_port(port: int, timeout: int = 15) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.25)
    raise RuntimeError(f"Port {port} did not open within {timeout}s")


def _wait_for_url(url: str, timeout: int = 60) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"{url} did not become ready within {timeout}s")


def _kill_existing() -> None:
    for port in (MOCK_PORT, STREAMLIT_PORT):
        try:
            subprocess.run(["pkill", "-f", f":{port}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    
    db_path = Path.home() / ".swarmmind" / "swarmmind.db"
    if db_path.exists():
        try:
            db_path.unlink()
        except Exception:
            pass
            
    time.sleep(1)


def cleanup() -> None:
    _kill_existing()
    # Restore backed-up config.toml if we replaced it
    config_dir = Path.home() / ".swarmmind"
    config_toml = config_dir / "config.toml"
    config_toml_backup = config_dir / "config.toml.demo_bak"
    if config_toml_backup.exists():
        config_toml.unlink(missing_ok=True)
        config_toml_backup.rename(config_toml)
    elif config_toml.exists():
        # Only remove if it's our demo config
        content = config_toml.read_text()
        if "127.0.0.1" in content and str(MOCK_PORT) in content:
            config_toml.unlink()


MOCK_SERVER_PROC: subprocess.Popen | None = None
STREAMLIT_PROC: subprocess.Popen | None = None


def start_mock_server() -> None:
    global MOCK_SERVER_PROC
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    MOCK_SERVER_PROC = subprocess.Popen(
        [sys.executable, __file__, "--mock-server", str(MOCK_PORT)],
        cwd=str(PROJECT_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_port(MOCK_PORT, timeout=15)
    print(f" Mock Lemonade → http://127.0.0.1:{MOCK_PORT}")


def start_streamlit() -> None:
    global STREAMLIT_PROC

    # Write a temporary config.toml so Lemonade points at the mock server.
    # pydantic-settings reads ~/.swarmmind/config.toml — this ensures the
    # FIRST health check on app startup hits our mock and returns "ok".
    config_dir = Path.home() / ".swarmmind"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_toml = config_dir / "config.toml"
    config_toml_backup = config_dir / "config.toml.demo_bak"

    # Back up existing config if present
    if config_toml.exists():
        config_toml.rename(config_toml_backup)

    config_toml.write_text(
        f"[lemonade]\nhost = \"127.0.0.1\"\nport = {MOCK_PORT}\n"
    )

    env = os.environ.copy()
    env["SWARMMIND_LEMONADE__PORT"] = str(MOCK_PORT)   # nested pydantic-settings
    env["SWARMMIND_LEMONADE__HOST"] = "127.0.0.1"
    env["STREAMLIT_SERVER_HEADLESS"] = "true"
    log_file = open(PROJECT_DIR / "demo_output" / "streamlit.log", "w")
    STREAMLIT_PROC = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(PROJECT_DIR / "swarmmind" / "ui" / "app.py"),
            "--server.port",
            str(STREAMLIT_PORT),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
            "--theme.base",
            "dark",
        ],
        cwd=str(PROJECT_DIR),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    _wait_for_url(f"http://127.0.0.1:{STREAMLIT_PORT}", timeout=60)
    # Extra settle time so Streamlit fully renders + connection check completes
    time.sleep(4)
    print(f" Streamlit UI → http://127.0.0.1:{STREAMLIT_PORT}")


# ---------------------------------------------------------------------------
# Screenshot / frame helpers
# ---------------------------------------------------------------------------
FRAME_DIR.mkdir(parents=True, exist_ok=True)
frame_counter = [0]


def record_frame(page, full_page: bool = False) -> None:
    path = FRAME_DIR / f"frame_{frame_counter[0]:05d}.jpeg"
    try:
        page.screenshot(path=str(path), full_page=full_page, type="jpeg", quality=70, timeout=1000)
    except Exception as exc:
        print(f" frame capture failed: {exc}", flush=True)
    frame_counter[0] += 1


def snap(name: str, page, full_page: bool = False) -> None:
    path = SCREENSHOT_DIR / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=full_page)
    except Exception as exc:
        print(f" screenshot failed: {exc}")


# ---------------------------------------------------------------------------
# Helper: reliable eval-based interaction
# ---------------------------------------------------------------------------
def _fill_textarea(page, text: str) -> bool:
    try:
        loc = page.locator("textarea").first
        loc.fill(text, force=True)
        loc.blur()
        return True
    except Exception:
        return False


def _fill_textbox(page, text: str, index: int = 0) -> bool:
    try:
        loc = page.locator('input[type="text"], input:not([type])').nth(index)
        loc.fill(text, force=True)
        loc.blur()
        return True
    except Exception:
        return False


def _fill_textbox_by_label(page, label_text: str, value: str) -> bool:
    """Fill a Streamlit text_input by its visible label."""
    try:
        # Find the stTextInput div that contains the label text, then find its input
        loc = page.locator(f"xpath=//div[@data-testid='stTextInput' and .//*[normalize-space(text())='{label_text}']]//input").first
        loc.fill(value, force=True)
        loc.blur()
        return True
    except Exception as e:
        print(f"Failed to fill {label_text}: {e}")
        return False


def _st_scroll(page, target_y: int) -> None:
    page.mouse.move(1000, 500)
    # Just wheel heavily to top if target is 0
    if target_y == 0:
        page.mouse.wheel(0, -10000)
    else:
        page.mouse.wheel(0, target_y)


def _st_scroll_by(page, dy: int) -> None:
    page.mouse.move(1000, 500)
    page.mouse.wheel(0, dy)



def _click_button(page, label_substring: str) -> bool:
    """Click a button whose text contains label_substring — JS-based for reliability."""
    return bool(
        page.evaluate(
            """(sub) => {
                const btns = document.querySelectorAll('button');
                for (const b of btns) {
                    if (b.innerText.includes(sub)) { b.click(); return true; }
                }
                return false;
            }""",
            label_substring,
        )
    )


def _click_summary(page, label_substring: str = "") -> bool:
    """Click the first <summary> element, or the one whose text contains label_substring."""
    try:
        if label_substring:
            page.locator(f"summary:has-text('{label_substring}')").first.click(timeout=2000)
        else:
            page.locator("summary").first.click(timeout=2000)
        return True
    except Exception as e:
        print(f"Failed to click summary '{label_substring}': {e}")
        return False


# ---------------------------------------------------------------------------
# Capture flow
# ---------------------------------------------------------------------------
def _capture() -> None:
    """Extended demo capture — ~900 frames for a 90-second 10fps video."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415
    
    # Helpers to reduce repetition
    FINT = 0.12  # frame interval (~8 fps capture -> 10 fps video)
    def wait(n: int):
        for _ in range(n):
            record_frame(page)
            time.sleep(FINT)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=2,
        )
        page = context.new_page()
        page.set_default_timeout(15000)

        # ==================================================================
        # Scene 0: Title Card
        # ==================================================================
        print("\n--- Scene 0: Title Card ---")
        title_html = """
        <html>
        <body style="background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); color: white; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0;">
            <div style="text-align: center;">
                <h1 style="font-size: 5rem; margin-bottom: 20px; background: -webkit-linear-gradient(#fff, #aaa); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">SwarmMind</h1>
                <h2 style="font-size: 2.5rem; font-weight: 300; color: #a5b4fc;">AMD Lemonade Developer Challenge</h2>
                <div style="margin-top: 50px; font-size: 1.5rem; color: #94a3b8;">Autonomous AI Research Assistant</div>
            </div>
        </body>
        </html>
        """
        page.set_content(title_html)
        wait(25)
    
        page.goto(f"http://127.0.0.1:{STREAMLIT_PORT}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle", timeout=30000)
    
        # ==================================================================
        # Scene 1 — Landing / Intro  (≈12 s  ≈120 frames)
        # ==================================================================
        print("\n--- Scene 1: Landing / Intro ---")
        # Let the UI fully settle — health check re-runs until server is OK
        # Streamlit needs a couple of re-runs to pick up the connection status
        wait(80)  # ~10 seconds for health check to resolve
        snap("01-landing", page)
        # Hold on the welcome view a bit longer
        wait(40)
        snap("01b-landing-held", page)
    
        # ==================================================================
        # Scene 2 — Create Project  (≈18 s  ≈150 frames)
        # ==================================================================
        print("\n--- Scene 2: Create Project ---")
        try:
            # Click the Create Project expander to open it
            page.evaluate("""() => {
                const summaries = Array.from(document.querySelectorAll('summary'));
                const summary = summaries.find(s => s.textContent.includes('Create Project'));
                if (summary) summary.click();
            }""")
            wait(15)
            # Fill project name by targeting the aria-label
            loc_name = page.get_by_label("Project name", exact=True)
            loc_name.fill("AMD AI Research", force=True)
            loc_name.press("Enter")
            wait(20)
            snap("02a-project-name", page)
            # Fill description
            desc = "Exploring AMD's AI accelerator ecosystem including Ryzen AI NPU, ROCm GPU compute, and Strix Halo processors."
            loc_desc = page.get_by_label("Description", exact=True)
            loc_desc.fill(desc, force=True)
            loc_desc.press("Enter")
            wait(30)
            snap("02b-desc-filled", page)
            # Click Create and capture the transition
            _click_button(page, "Create")
            wait(30)
            # Close the Create Project expander so it doesn't stay open
            _click_summary(page, "Create Project")
            wait(30)
            snap("02c-creating", page)
            # Hold on the 3-panel UI after creation
            wait(40)
            snap("02d-project-ready", page)
            print("  Project created")
        except Exception as exc:
            print(f"  Project creation skipped: {exc}")
    
        # ==================================================================
        # Scene 3 — Swarm Configuration  (≈12 s  ≈100 frames)
        # ==================================================================
        print("\n--- Scene 3: Swarm Config ---")
        try:
            # Click the Swarm Configuration summary
            _click_summary(page, "Swarm Configuration")
            wait(30)
            snap("03a-config", page)
            print("  Config expanded")
        except Exception as exc:
            print(f"  Config skipped: {exc}")
    
        # ==================================================================
        # Scene 4 — Enter Query  (≈14 s  ≈120 frames)
        # ==================================================================
        print("\n--- Scene 4: Query Input ---")
        query_text = (
            "What are the most recent features added to ROCm 6.x and "
            "how do they impact LLM inference performance?"
        )
        try:
            if not _fill_textarea(page, query_text):
                _fill_textbox(page, query_text)
            wait(80)
            snap("04-query", page)
            print("  Query entered")
        except Exception as exc:
            print(f"  Query input skipped: {exc}")
    
        # ==================================================================
        # Scene 5 — Launch Swarm  (≈30 s  ≈360 frames)
        #   Mock delays: conductor 3s, workers 4-5s each, synthesis 3s
        #   Total process ≈ 11-13s wall-clock
        # ==================================================================
        print("\n--- Scene 5: Launch Swarm ---")
        try:
            _click_button(page, "Launch Swarm")
            # Scroll down to ensure the progress area is visible below config
            _st_scroll_by(page, 400)
            wait(5)
            
            # Preflight / connecting
            snap("05a-preflight", page)
            
            # Conductor decomposing (runs for 12s, wait ~6s = 20 frames)
            wait(20)  
            snap("05b-conductor", page)
            
            # Workers running (runs 12s -> 28s, wait ~21s = 70 frames total)
            wait(50)  
            snap("05c-workers", page)
            
            # Synthesis phase (runs 28s -> 40s, wait ~33s = ~90 frames total)
            wait(15)  
            snap("05e-synthesis", page)
            
            # Report appears (after 40s, wait ~45s = ~150 frames total)
            wait(65)
            
            # Scroll up to top to begin report reveal
            _st_scroll(page, 0)
            wait(15)
            snap("05f-report", page)
            
            # Slowly reveal the full report by scrolling down frame by frame
            for step in range(50):
                _st_scroll_by(page, 50)  # 50px per step
                record_frame(page)
                time.sleep(0.07)
            
            # Hold at mid-report
            wait(20)
            snap("05g-report-held", page)
            
            # Continue scrolling to reveal more
            for step in range(30):
                _st_scroll_by(page, 60)
                record_frame(page)
                time.sleep(0.07)
                
            print("  Swarm completed")
        except Exception as exc:
            print(f"  Launch Swarm skipped: {exc}")
    
        # ==================================================================
        # Scene 6 — Explore Report (Scroll)  (≈6 s  ≈60 frames)
        # ==================================================================
        print("\n--- Scene 6: Explore Report ---")
        try:
            # Scroll back to top
            _st_scroll(page, 0)
            wait(10)
            # Smooth scroll through the full report
            for step in range(70):
                _st_scroll_by(page, 55)  # 55px each step
                record_frame(page)
                time.sleep(0.05)
            # Hold at bottom
            wait(20)
            snap("06-report", page)
            # Scroll back to top for next scene
            _st_scroll(page, 0)
            wait(15)
            print("  Report explored")
        except Exception as exc:
            print(f"  Report scroll skipped: {exc}")
    
        # ==================================================================
        # Scene 7 — Follow-up  (≈12 s  ≈100 frames)
        # ==================================================================
        print("\n--- Scene 7: Follow-up ---")
        try:
            # Click a follow-up question
            _click_button(page, "?")
            wait(20)
            snap("07a-followup-clicked", page)
            # And launch again
            _click_button(page, "Launch Swarm")
            wait(80)
            snap("07b-followup-swarm", page)
            print("  Follow-up done")
        except Exception as exc:
            print(f"  Follow-up skipped: {exc}")
    
        # ==================================================================
        # Scene 8 — Backend / Studio  (≈10 s  ≈80 frames)
        # ==================================================================
        print("\n--- Scene 8: Backend / Studio ---")
        try:
            # Backend strategy
            _click_summary(page, "Backend")
            wait(25)
            snap("08a-backend", page)
            # Studio panel — scroll to top
            page.evaluate("window.scrollTo(0, 0)")
            wait(15)
            snap("08b-studio", page)
            print("  Backend & studio shown")
        except Exception as exc:
            print(f"  Backend/studio skipped: {exc}")
    
        # ==================================================================
        # Scene 9 — Outro / Wrap  (≈8 s  ≈60 frames)
        # ==================================================================
        print("\n--- Scene 9: Outro ---")
        wait(60)
        snap("09-outro", page)
        
        # ==================================================================
        # Scene 10: Outro Card
        # ==================================================================
        print("\n--- Scene 10: Outro Card ---")
        outro_html = """
        <html>
        <body style="background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); color: white; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0;">
            <div style="text-align: center;">
                <h1 style="font-size: 4rem; margin-bottom: 20px; background: -webkit-linear-gradient(#fff, #aaa); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">Built with AMD Technologies</h1>
                <h2 style="font-size: 2rem; font-weight: 300; color: #a5b4fc;">Ryzen AI NPU &bull; ROCm &bull; Strix Halo</h2>
                <div style="margin-top: 50px; font-size: 1.5rem; color: #94a3b8;">Thank you for watching!</div>
            </div>
        </body>
        </html>
        """
        page.set_content(outro_html)
        wait(30)
        print("  Done")

        context.close()
        browser.close()



# ---------------------------------------------------------------------------
# Compile video
# ---------------------------------------------------------------------------
def compile_video() -> None:
    """Compile frames into MP4 using ffmpeg directly for reliable fps control."""
    FRAME_DIR.mkdir(parents=True, exist_ok=True)
    frames = sorted(FRAME_DIR.glob("frame_*.jpeg"))
    if not frames:
        print("[compile] No frames - skipping video compilation")
        return
    video_path = VIDEO_DIR / "swarmmind_demo.mp4"
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[compile] {len(frames)} frames -> {video_path}")

    list_file = FRAME_DIR / "_concat.txt"
    list_file.write_text(
        "\n".join(f"file '{f.name}'" for f in frames) + "\n",
        encoding="utf-8",
    )

    cmd = [
        "ffmpeg", "-y",
        "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-r", "10",
        "-i", str(list_file),
        "-stream_loop", "-1",
        "-i", str(PROJECT_DIR / "demo_output" / "music.mp3"),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "15",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-r", "10",
        "-movflags", "+faststart",
        str(video_path),
    ]
    print("[compile] running ffmpeg...")
    proc = subprocess.run(cmd, cwd=str(FRAME_DIR))
    list_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        print(f"[compile] ffmpeg failed (exit {proc.returncode})")
        return
    size_mb = video_path.stat().st_size / 1024 / 1024
    print(f"[compile] Video: {video_path.name} ({size_mb:.1f} MB)")

# ---------------------------------------------------------------------------
# Entrypoints
# ---------------------------------------------------------------------------
def run_mock_server(port: int) -> None:
    class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = ThreadedServer(("127.0.0.1", port), DemoHandler)
    print(f"AMD demo mock → http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


def main() -> None:
    print("=" * 60)
    print(" SwarmMind Demo Capture")
    print("=" * 60)

    print("\n[1/4] Cleaning up old processes…")
    _kill_existing()

    print("\n[2/4] Starting enhanced mock Lemonade server…")
    signal.signal(signal.SIGTERM, lambda signum, frame: (_kill_existing(), sys.exit(0)))
    signal.signal(signal.SIGINT, lambda signum, frame: (_kill_existing(), sys.exit(0)))
    start_mock_server()

    print("\n[3/4] Starting Streamlit…")
    start_streamlit()

    print("\n[4/4] Running Playwright capture…")
    try:
        _capture()
    except Exception as exc:
        print(f" Capture failed: {exc}")
    finally:
        print("\n Cleaning up…")
        _kill_existing()

    print("\n[5/4] Compiling video…")
    compile_video()
    print("=" * 60)
    print(" Demo capture complete!")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--mock-server":
        run_mock_server(int(sys.argv[2]))
    else:
        main()
