#!/usr/bin/env python3
"""SwarmMind demo video editor.

Reads the screenshots + swarm frames captured by ``demo_capture.py`` and
composes them into a 1920x1080 polished demo video with:

* Title card  →  hook card  →  scenes (with lower-third labels)
* A live phase-timeline strip ("Conductor ▸ Workers ▸ Synthesis") that
  lights up as the swarm-run frames play
* Generative ambient music track (no external file needed)
* End card with credits + URL

Usage::

    python scripts/demo_edit.py            # build the final video
    python scripts/demo_edit.py --no-music # silent build

Output is written to ``demo_output/video/swarmmind_demo.mp4``.
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import wave
from io import BytesIO
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = PROJECT_DIR / "demo_output" / "screenshots"
FRAME_DIR = SCREENSHOT_DIR / "frames"
VIDEO_DIR = PROJECT_DIR / "demo_output" / "video"
TEMP_FRAME_DIR = Path("/tmp/swarmmind_frames_build")
MUSIC_DIR = Path("/tmp/swarmmind_music")

W, H = 1920, 1080
FPS = 30
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Brand palette (kept cohesive with the Streamlit UI dark theme)
BG_DARK = (15, 23, 42)         # #0f172a
BG_PANEL = (30, 41, 59)        # #1e293b
ACCENT = (99, 102, 241)        # #6366f1  indigo
ACCENT_BRIGHT = (129, 140, 248)
WARM = (250, 204, 21)          # #facc15  yellow
TEXT = (248, 250, 252)         # #f8fafc
TEXT_DIM = (148, 163, 184)     # #94a3b8

# Story arc -----------------------------------------------------------------
TITLE_CARD_DUR = 4.0
HOOK_CARD_DUR = 4.0
SCENE_DUR = 4.0          # static label scenes
SWARM_DUR_PER_FRAME = 0.12  # ~30 frames captured ≈ 4s at 0.12 each
END_CARD_DUR = 6.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def check_ffmpeg() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise RuntimeError("ffmpeg not found — run: sudo apt install ffmpeg")
    return p


def load_fonts():
    from PIL import ImageFont

    return {
        "title": ImageFont.truetype(FONT_BOLD, 110),
        "subtitle": ImageFont.truetype(FONT_REG, 36),
        "label": ImageFont.truetype(FONT_BOLD, 32),
        "scene": ImageFont.truetype(FONT_BOLD, 38),
        "scene_sub": ImageFont.truetype(FONT_REG, 24),
        "credit": ImageFont.truetype(FONT_REG, 26),
    }


def resize_cover(img, target_w: int, target_h: int):
    """Resize+center-crop ``img`` to fill ``target_w`` × ``target_h`` (LANCZOS)."""
    from PIL import Image

    sw, sh = img.size
    scale = max(target_w / sw, target_h / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    x, y = (nw - target_w) // 2, (nh - target_h) // 2
    return img.crop((x, y, x + target_w, y + target_h))


def draw_centered_text(draw, text, font, y, fill=TEXT, w=W):
    """Draw a single line of text horizontally centered at vertical pixel ``y``."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (w - tw) // 2 - bbox[0]
    draw.text((x, y), text, font=font, fill=fill)
    return th


def rounded_rect(draw, xy, radius=16, fill=None, outline=None, width=2):
    """Pillow ≥8.2 ships rounded_rectangle; on older builds fall back to rect."""
    try:
        draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)
    except AttributeError:
        draw.rectangle(xy, fill=fill, outline=outline, width=width)


# ---------------------------------------------------------------------------
# Card / overlay rendering
# ---------------------------------------------------------------------------
def compose_title_card(fonts) -> "PIL.Image.Image":
    """Big title with gradient bar + logo glyph + subtitle."""
    from PIL import Image, ImageDraw

    bg = Image.new("RGB", (W, H), BG_DARK)
    d = ImageDraw.Draw(bg)

    # Soft top accent bar
    # Clean top bar with brand colour
    d.rectangle([0, 0, W, 6], fill=ACCENT)
    # Title
    title = "SwarmMind"
    th = draw_centered_text(d, title, fonts["title"], y=370, fill=TEXT)
    bar_y = 370 + th + 24
    d.rectangle([W // 2 - 80, bar_y, W // 2 + 80, bar_y + 5], fill=ACCENT)
    # Subtitle
    draw_centered_text(
        d,
        "Multi-Agent Research on your AMD Hardware",
        fonts["subtitle"],
        y=bar_y + 30,
        fill=TEXT_DIM,
    )
    # Tag pill
    pill_w, pill_h = 320, 50
    px = (W - pill_w) // 2
    py = H - 140
    rounded_rect(d, [px, py, px + pill_w, py + pill_h], radius=25, fill=BG_PANEL)
    d.text(
        (px + 24, py + 7),
        "POWERED  BY  LEMONADE  SDK",
        font=fonts["credit"],
        fill=ACCENT_BRIGHT,
    )
    return bg


def compose_hook_card(fonts) -> "PIL.Image.Image":
    """Hook line that introduces what the viewer is about to see."""
    from PIL import Image, ImageDraw

    bg = Image.new("RGB", (W, H), BG_DARK)
    d = ImageDraw.Draw(bg)

    d.rectangle([0, 0, W, 6], fill=ACCENT)

    text = "Ask anything."
    draw_centered_text(d, text, fonts["title"], y=320, fill=TEXT)

    text2 = "Get a multi-agent research report."
    draw_centered_text(d, text2, fonts["subtitle"], y=520, fill=WARM)

    # Hint at the structure
    y = 740
    for line in [
        "1.  Type your question",
        "2.  Watch a swarm of agents work in parallel",
        "3.  Receive a synthesised, sourced report",
    ]:
        d.text((W // 2 - 320, y), line, font=fonts["label"], fill=TEXT_DIM)
        y += 52

    return bg


def end_card(fonts) -> "PIL.Image.Image":
    """Credits + URL."""
    from PIL import Image, ImageDraw
    bg = Image.new("RGB", (W, H), BG_DARK)
    d = ImageDraw.Draw(bg)
    d.rectangle([0, 0, W, 6], fill=ACCENT)
    draw_centered_text(d, "Thank you", fonts["title"], y=460, fill=TEXT)
    draw_centered_text(d, "github.com/rahulgupta0-dev/swarmmind",
                       fonts["subtitle"], y=620, fill=TEXT_DIM)
    draw_centered_text(d, "Built on Lemonade SDK  •  AMD Ryzen AI",
                       fonts["credit"], y=720, fill=TEXT_DIM)
    return bg


def overlay_lower_third(base, text, sub, fonts, progress=0.0):
    """Add a dark indigo lower-third label bar to a 1920x1080 frame.
    ``progress`` (0..1) drives a slide-in animation: at 0 the label is
    off-screen below, at 1 it is parked at its resting position.
    """
    from PIL import Image, ImageDraw
    # Work in RGBA so we can use semi-transparent fills
    if base.mode != "RGBA":
        canvas = base.convert("RGBA")
    else:
        canvas = base.copy()
    d = ImageDraw.Draw(canvas)
    bar_h = 110
    rest_y = H - bar_h - 24
    # slide up from below: at progress=0 cur_y=H, at progress=1 cur_y=rest_y
    cur_y = int(H - (H - rest_y) * progress)
    cur_y = max(0, min(H - bar_h, cur_y))
    # Semi-transparent dark bar
    rounded_rect(
        d,
        [60, cur_y, W - 60, cur_y + bar_h],
        radius=14,
        fill=(20, 33, 65, 200),
    )
    # Left accent strip
    d.rectangle([60, cur_y, 90, cur_y + bar_h], fill=(99, 102, 241, 255))
    # Heading text
    d.text((124, cur_y + 16), text, font=fonts["scene"], fill=(248, 250, 252, 255))
    d.text((124, cur_y + 64), sub, font=fonts["scene_sub"], fill=(148, 163, 184, 255))
    return canvas.convert("RGB")


def overlay_phase_strip(base, phase_idx, fonts):
    """Overlay the swarm-run phase indicator ('Conductor ▸ Workers ▸ Synthesis')."""
    from PIL import Image, ImageDraw

    out = base.copy()
    d = ImageDraw.Draw(out)

    labels = ["Conductor", "Workers", "Synthesis"]
    box_w = 280
    box_h = 70
    gap = 30
    total_w = box_w * 3 + gap * 2
    start_x = (W - total_w) // 2
    y = 40

    for i, lab in enumerate(labels):
        x = start_x + i * (box_w + gap)
        active = i == phase_idx
        done = i < phase_idx
        fill = (40, 65, 140) if done else (BG_PANEL if not active else ACCENT)
        text_col = TEXT if (active or done) else TEXT_DIM
        rounded_rect(d, [x, y, x + box_w, y + box_h], radius=12, fill=fill)
        bbox = d.textbbox((0, 0), lab, font=fonts["scene"])
        tw = bbox[2] - bbox[0]
        d.text(
            (x + (box_w - tw) // 2 - bbox[0], y + (box_h - (bbox[3] - bbox[1])) // 2 - bbox[1]),
            lab,
            font=fonts["scene"],
            fill=text_col,
        )

    return out


def paste_screenshot(screenshot_path, label, sub, fonts,
                     lower_third_progress=1.0):
    """Open a screenshot, fit it into 1920x1080, then add a lower-third."""
    from PIL import Image
    s = Image.open(screenshot_path).convert("RGB")
    fitted = resize_cover(s, W, H)
    return overlay_lower_third(fitted, label, sub, fonts, lower_third_progress)
# Generative music track
# ---------------------------------------------------------------------------
def synth_music_wav(path: Path, duration: int = 45, sr: int = 44_100):
    """Generate a warm ambient pad (A minor chord) with soft pulse using numpy.
    Fully vectorised — ~220s of audio computes in <1 s.
    ffmpeg will stream-loop it to cover the full video.
    """
    import wave
    import numpy as np  # noqa: PLC0415
    n = sr * duration
    t = np.arange(n, dtype=np.float64) / sr
    # A-minor chord voices (two detuned copies each for warmth)
    voices = [220.0, 261.63, 329.63, 392.0, 440.0, 523.25]
    pad = np.zeros(n, dtype=np.float64)
    for f in voices:
        pad += np.sin(2 * np.pi * f * t) * 0.10
        pad += np.sin(2 * np.pi * f * 1.003 * t + 0.7) * 0.06
    pad /= len(voices)
    # Swelling envelope (~6 s cycle)
    env = 0.50 + 0.50 * np.sin(2 * np.pi * (1.0 / 6.0) * t - np.pi / 2)
    audio = pad * env
    # Soft kick thump every quarter note (bpm=72, beat=0.833s)
    beat = 60.0 / 72
    kick_phase = (t % beat) / beat
    k_env = np.exp(-kick_phase * 7.0)
    kick = np.where(kick_phase < 0.20,
                    np.sin(2 * np.pi * 80.0 * t) * k_env * 0.15,
                    0.0)
    audio += kick
    # Quiet hat taps on the off-beats
    eighth = beat / 2
    hat_phase = (t % eighth) / eighth
    h_env = np.exp(-hat_phase * 18.0)
    rng = np.random.default_rng(0xBEEF)
    hat = np.where(hat_phase < 0.12,
                   rng.uniform(-0.3, 0.3, n) * h_env * 0.03,
                   0.0)
    audio += hat
    # Gentle limiter
    audio = np.clip(audio, -0.92, 0.92)
    # Stereo mix (slight reverb shimmer by adding a 40ms delayed copy)
    delay_samps = int(0.040 * sr)
    reverb = np.zeros(n, dtype=np.float64)
    reverb[delay_samps:] = audio[:-delay_samps] * 0.25
    stereo = np.stack([audio + reverb * 0.3, audio - reverb * 0.3], axis=1)
    # Convert to 16-bit PCM and write WAV
    path.parent.mkdir(parents=True, exist_ok=True)
    stereo_i16 = np.int16(np.round(stereo * 32_000))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo_i16.tobytes())
    return path


# ---------------------------------------------------------------------------
# Timeline composition
# ---------------------------------------------------------------------------
# Scene list — order + labels tell the story.  Each (file, label, sub).
SCENES = [
    ("01-landing.png",      "Hardware auto-detected",
        "AMD Ryzen AI NPU  •  Radeon GPU  •  64 GB RAM"),
    ("01b-landing-held.png", "Welcome to SwarmMind",
        "Your local AI research assistant"),
    ("02a-project-name.png",  "Step 1 — name your project",
        "AMD AI Research"),
    ("02b-desc-filled.png",  "Describe the scope",
        "Adding context helps the swarm plan its attack"),
    ("02d-project-ready.png", "Project created",
        "Three-panel workspace is now active"),
    ("03a-config.png",       "Swarm configuration",
        "Model, workers, decomposition depth"),
    ("04-query.png",         "Type your research question",
        "The conductor will decompose it into parallel sub-tasks"),
    ("05a-preflight.png",    "Preflight check",
        "Connecting to Lemonade SDK on localhost:13305"),
    ("05b-conductor.png",    "Conductor decomposed the query",
        "3 sub-tasks fired off in parallel"),
    ("05c-workers.png",      "Workers running in parallel",
        "web  •  rag  •  analysis"),
    ("05d-workers-completing.png", "Workers wrapping up",
        "Findings streaming back to the conductor"),
    ("05e-synthesis.png",    "Synthesis engine merges results",
        "Resolves contradictions, ranks confidence"),
    ("05f-report.png",       "Report ready",
        "Executive summary + sourced sections"),
    ("05g-report-held.png",  "Full research report",
        "Sections, contradictions and follow-ups"),
    ("06a-report-scrolled.png", "Scroll through findings",
        "Each section cites its sources"),
    ("06b-report-top.png",   "Back to the top",
        "Notice the follow-up prompts"),
    ("07a-followup-clicked.png", "Click a follow-up",
        "Any prompt continues the conversation with context"),
    ("07b-followup-swarm.png",   "New swarm run on the follow-up",
        "Context is preserved between rounds"),
    ("08a-backend.png",      "Per-role backend assignment",
        "NPU  ▸  orchestration   •   GPU  ▸  inference"),
    ("08b-studio.png",       "Studio panel",
        "Diagrams, narration, export"),
    ("09-outro.png",         "See you next demo",
        ""),
]


def build_timeline(fonts) -> list:
    """Return list of (name, PIL.Image.Image) pairs for the whole timeline."""
    from PIL import Image

    sections: list = []
    sections.append(("title", compose_title_card(fonts)))
    sections.append(("hook",  compose_hook_card(fonts)))

    # Regular numbered scenes
    for idx, (fname, label, sub) in enumerate(SCENES, start=1):
        p = SCREENSHOT_DIR / fname
        if not p.exists():
            print(f"   ⚠  missing screenshot: {fname} — skipping")
            continue
        step_str = f"{idx:02d} / {len(SCENES):02d}"
        sub_with_step = sub if not sub else f"{sub}      │      {step_str}"
        sections.append(
            (f"scene_{idx}", paste_screenshot(
                p, label, sub_with_step, fonts,
                lower_third_progress=1.0,
            ))
        )

    # Swarm-run frames with live phase indicator
    swarm_frames = sorted(FRAME_DIR.glob("frame_*.png"))
    if swarm_frames:
        n = len(swarm_frames)
        phase_thresholds = [int(n * 0.10), int(n * 0.55), int(n * 0.92)]
        print(f"   📼  {n} swarm frames — phases at {phase_thresholds}")

        def phase_for(i: int) -> int:
            if i < phase_thresholds[0]:
                return 0
            if i < phase_thresholds[1]:
                return 1
            if i < phase_thresholds[2]:
                return 2
            return 2

        for i, fp in enumerate(swarm_frames):
            img = Image.open(fp).convert("RGB")
            fitted = resize_cover(img, W, H)
            labelled = overlay_phase_strip(fitted, phase_for(i), fonts)
            sections.append((f"swarm_{i:04d}", labelled))

    sections.append(("end", end_card(fonts)))
    return sections


# ---------------------------------------------------------------------------
# Frame write-out + encoding
# ---------------------------------------------------------------------------
def write_frames(sections) -> list:
    TEMP_FRAME_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    seq = 0
    per_section_dur = {
        "title": TITLE_CARD_DUR,
        "hook":  HOOK_CARD_DUR,
        "end":   END_CARD_DUR,
    }
    swarm_re = None
    for name, img in sections:
        if name.startswith("swarm_"):
            dur = SWARM_DUR_PER_FRAME
        elif name.startswith("scene_"):
            dur = SCENE_DUR
        else:
            dur = per_section_dur.get(name, 2.0)

        n = max(1, round(dur * FPS))
        buf = BytesIO()
        img.convert("RGB").save(buf, format="PNG", optimize=False)
        data = buf.getvalue()
        for _ in range(n):
            p = TEMP_FRAME_DIR / f"frame_{seq:05d}.png"
            p.write_bytes(data)
            files.append(p)
            seq += 1

    print(f"   wrote {len(files)} temp frames into {TEMP_FRAME_DIR}")
    return files


def encode(pat: str, audio: str | None, out: Path):
    ffmpeg = check_ffmpeg()
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
        "-framerate", str(FPS), "-i", pat,
    ]
    if audio and Path(audio).exists():
        cmd += ["-stream_loop", "-1", "-i", audio]
    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
    ]
    if audio and Path(audio).exists():
        cmd += [
            "-filter:a", "volume=0.25",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
        ]
    cmd += ["-movflags", "+faststart", str(out)]
    print(f"   encoding → {out.name}  (crf=18, {FPS}fps, {W}x{H})")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("FFMPEG STDERR tail:")
        print("\n".join(r.stderr.splitlines()[-30:]))
        raise RuntimeError(f"encode failed exit {r.returncode}")
    print(f"   ✓  encoded {out.stat().st_size / 1024 / 1024:.2f} MB")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-music", action="store_true",
                        help="skip music generation (silent video)")
    parser.add_argument("--output", type=str, default=None,
                        help="override output filename")
    args = parser.parse_args()

    print("=" * 60)
    print("  SwarmMind demo editor")
    print("=" * 60)

    if not FRAME_DIR.exists():
        sys.exit(f"❌  no frames at {FRAME_DIR} — run demo_capture.py first")

    nf = len(list(FRAME_DIR.glob("frame_*.png")))
    nshot = len([p for p in SCREENSHOT_DIR.glob("*.png") if not p.name.startswith("frame_")])
    print(f"\n  frames       : {nf}")
    print(f"  screenshots  : {nshot}")
    print(f"  target       : {W}x{H} @{FPS}fps")

    if TEMP_FRAME_DIR.exists():
        shutil.rmtree(TEMP_FRAME_DIR)
    TEMP_FRAME_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    fonts = load_fonts()
    print("\n  composing timeline …")
    sections = build_timeline(fonts)
    dur_total = sum(SCENE_DUR if n.startswith("scene_") else
                    SWARM_DUR_PER_FRAME if n.startswith("swarm_") else
                    {True: TITLE_CARD_DUR, False: HOOK_CARD_DUR}.get(n == "title", HOOK_CARD_DUR)
                    if n in ("title", "hook") else
                    END_CARD_DUR if n == "end" else 2.0
                    for n, _ in sections)
    print(f"  scenes={sum(1 for n,_ in sections if n.startswith('scene_'))}  "
          f"swarm_frames={sum(1 for n,_ in sections if n.startswith('swarm_'))}  "
          f"duration≈{dur_total:.1f}s")

    print("\n  writing temp frames …")
    write_frames(sections)

    audio = None
    satellite = Path("/tmp/swarmmind_music/Satellite_Logic.mp3")
    if satellite.exists():
        sz = satellite.stat().st_size // 1024
        print(f"\n  music: Satellite_Logic.mp3  ({sz} KB)")
        audio = satellite
    elif not args.no_music:
        print("\n  generating ambient music …")
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        audio = MUSIC_DIR / "ambient.wav"
        synth_music_wav(audio, duration=max(180, int(dur_total + 30)))
        print(f"   ✓  {audio}  ({audio.stat().st_size // 1024} KB)")
    else:
        print("\n  music: skipped (--no-music)")

    out = VIDEO_DIR / (args.output or "swarmmind_demo.mp4")
    encode(str(TEMP_FRAME_DIR / "frame_%05d.png"), audio, out)

    print("\n" + "=" * 60)
    print(f"  ✅  {out}  ({out.stat().st_size / 1024 / 1024:.2f} MB, ~{dur_total:.0f}s)")
    print("=" * 60)

    shutil.rmtree(TEMP_FRAME_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
