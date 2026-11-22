#!/usr/bin/env bash
# Smoke test for Lemonade SwarmMind
# Run this after starting your Lemonade server
# Usage: bash tests/smoke_test.sh

set -e

cd "$(dirname "$0")/.."
VENV=".venv/bin"

echo "============================================"
echo "  Lemonade SwarmMind — Smoke Test"
echo "============================================"
echo ""

# 1. Check Lemonade is running
echo "🔍 [1/6] Checking Lemonade server..."
LEMONADE_URL="${LEMONADE_URL:-http://localhost:13305}"
HEALTH="$($VENV/python -c "
import httpx
try:
    r = httpx.get('${LEMONADE_URL}/v1/health', timeout=5)
    print(f'OK ({r.status_code})')
except Exception as e:
    print(f'FAIL: {e}')
")"
echo "       ${LEMONADE_URL}/v1/health → ${HEALTH}"
if [[ "$HEALTH" == FAIL* ]]; then
    echo "       ❌ Lemonade is not running. Start it with: lemonade start"
    echo "       Or set LEMONADE_URL to your server address."
    exit 1
fi
echo ""

# 2. Check models are loaded
echo "🔍 [2/6] Checking loaded models..."
$VENV/python -c "
from swarmmind.config import Config
from swarmmind.lemonade.client import LemonadeClient
import asyncio

async def check():
    cfg = Config()
    client = LemonadeClient(cfg.get_lemonade_base_url())
    models = await client.get_models()
    if isinstance(models, list):
        names = [m.get('id', m.get('name', '?')) for m in models[:5]]
        print(f'       Found {len(models)} models: {\", \".join(names)}...')
    else:
        print(f'       Models response: {str(models)[:200]}')
    await client.close()

asyncio.run(check())
" 2>&1
echo ""

# 3. Test chat completion
echo "🔍 [3/6] Testing chat completion..."
$VENV/python -c "
from swarmmind.lemonade.client import LemonadeClient
import asyncio, json

async def check():
    client = LemonadeClient('${LEMONADE_URL}')
    response = await client.chat_completion(
        model='Gemma-4-12B-it',
        messages=[{'role': 'user', 'content': 'Say hello in one word'}],
        stream=False,
    )
    content = response['choices'][0]['message']['content']
    print(f'       Response: \"{content.strip()}\"')
    await client.close()

asyncio.run(check())
" 2>&1
echo ""

# 4. Test embeddings
echo "🔍 [4/6] Testing embeddings..."
$VENV/python -c "
from swarmmind.lemonade.client import LemonadeClient
import asyncio

async def check():
    client = LemonadeClient('${LEMONADE_URL}')
    emb = await client.embeddings('nomic-embed-text-v1-GGUF', 'test query')
    print(f'       Embedding dims: {len(emb[0])}')
    await client.close()

asyncio.run(check())
" 2>&1
echo ""

# 5. Test full CLI pipeline end-to-end, with real citations asserted
echo "🔍 [5/6] Testing CLI pipeline (grounded, cited report)..."
SMOKE_ID="smoke-proj-$$-$(date +%s)"
SMOKE_FACT="The capital of France is Paris."
$VENV/swarmmind project create "Smoke Test Project $SMOKE_ID" --id "$SMOKE_ID" 2>&1 || {
    echo "       ❌ project create failed"
    exit 1
}
$VENV/swarmmind source add "$SMOKE_ID" text "$SMOKE_FACT" --name "Smoke fact" 2>&1 || {
    echo "       ❌ source add failed"
    exit 1
}
SMOKE_REPORT_JSON="$($VENV/swarmmind ask "What is the capital of France?" --project-id "$SMOKE_ID" --no-web --json 2>/dev/null)" || {
    echo "       ❌ ask failed"
    exit 1
}
export SMOKE_REPORT_JSON

$VENV/python - <<'PY'
import json
import os
import sys

raw = os.environ.get("SMOKE_REPORT_JSON", "")
if not raw.strip():
    print("       FAIL: ask produced no JSON report")
    sys.exit(1)
try:
    report = json.loads(raw)
except json.JSONDecodeError as exc:
    print(f"       FAIL: ask output is not valid JSON: {exc}")
    sys.exit(1)

failures: list[str] = []


def check(label: str, ok: bool) -> None:
    print(f"       {'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        failures.append(label)


sources = report.get("sources") or []
check("report has a non-empty sources list", isinstance(sources, list) and bool(sources))
source_ids = {
    str(s.get("id")) for s in sources if isinstance(s, dict) and s.get("id")
}

section_ids: list[str] = []
for section in report.get("sections") or []:
    if isinstance(section, dict):
        section_ids.extend(str(s) for s in (section.get("sources") or []))
check(
    "every sections[].sources ID exists in sources",
    bool(section_ids) and all(sid in source_ids for sid in section_ids),
)

grounding = report.get("grounding") or {}
try:
    total_claims = int(grounding.get("total_claims", 0))
except (TypeError, ValueError):
    total_claims = 0
check("grounding.total_claims > 0", total_claims > 0)

check(
    "title came from the mock synthesis path (not fallback)",
    report.get("title") == "Mock grounded research report",
)

sys.exit(1 if failures else 0)
PY
echo ""

# 6. Test Web UI starts
echo "🔍 [6/6] Testing Streamlit web UI starts..."
$VENV/python -c "
from swarmmind.ui.components.icons import icon_html
html = icon_html('check-circle', 'fill')
assert 'ph-fill ph-check-circle' in html
print(f'       Icons OK: {html}')
from swarmmind.ui.panels.chat import render_chat_panel
from swarmmind.ui.panels.studio import render_studio_panel
from swarmmind.ui.panels.sources import render_sources_panel
print('       UI panels import OK')
print('       You can launch the web UI with: swarmmind web')
" 2>&1
echo ""

echo "============================================"
echo "  ✅ Smoke Test Complete!"
echo "============================================"
