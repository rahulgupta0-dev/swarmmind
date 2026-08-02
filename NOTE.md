# SwarmMind — Planned Improvements for Researchers

> All three items below are **fixed** and verified (see status notes per section).

## 1. Wire Source Ingestion to the Pipeline

**Problem:** When a user adds a source (PDF, URL, YouTube, text) via the Sources panel (`ui/panels/sources.py:_handle_add_source`), it only saves a record to the SQLite `sources` table. It never calls `Pipeline.process_source()` from `rag/pipeline.py`, meaning the document is never converted to text, chunked, or indexed into ChromaDB. The RAG worker therefore searches an empty index, rendering the source addition useless.

**What exists:**
- `rag/pipeline.py` — `Pipeline` class with `process_source()` that handles conversion via MarkItDown, chunking, and ChromaDB storage.
- `rag/chroma_store.py` — `ChromaStore` for persisting chunks.
- `ui/panels/sources.py:_handle_add_source` — Currently stops at `db.create_source(src)`.

**Goal:** After saving the source record, call `Pipeline.process_source()` to ingest it into ChromaDB, and update the source status from `"pending"` → `"processing"` → `"ready"` (or `"error"`).

**Status: ✅ Fixed**
- `ui/panels/sources.py` — `_handle_add_source` now saves the source record, then fires a daemon `threading.Thread` running `_ingest_source_background(db_path, source_id, db_type, source_input, config_obj)`.
- `_ingest_source_background` opens its own `Database`, flips status `pending → processing`, runs `Pipeline.process_source()` via `asyncio.run()`, then updates the row to `ready` (with char/chunk counts) or `error` (with the exception message). The exception path reuses the same DB connection (no second Database/lock).
- Pasted **text** sources are encoded to bytes before ingestion so Pipeline writes a temp `.txt` and MarkItDown converts it (a bare string would be treated as a file path and fail). Real file paths pass through unchanged. Verified end-to-end: pasted text → `ready` (1 chunk), file path → `ready`, forced error → `error` with message.
- `data/database.py` — added `update_source_status(source_id, status, char_count=0, chunk_count=0, error_message=None)`.
- Known limitation: no live status refresh — the status column updates in the DB but the page shows the new status on the next user interaction (the success banner is also wiped by the rerun).

---

## 2. Report Persistence

**Problem:** Research reports live only in Streamlit's session state. A page refresh (or browser close) wipes all conversation history and past reports. There is no way to browse, search, or revisit old reports.

**What exists:**
- `data/database.py` — `Database` class with a `conversations` table that stores query metadata (no report content yet).
- `data/models.py` — `Conversation` model with `query`, `web_search_used`, `worker_count`, `created_at`.
- Report is a JSON dict with `title`, `executive_summary`, `sections`, `conclusion`, `contradictions`, `follow_up_questions`.
- CLI placeholders at `cli/main.py:337-346` — `report show` and `report export` say "coming in Phase 2."

**Goal:** Save the full report JSON alongside the conversation record, enable browsing past reports in the UI, and implement the CLI `report show` and `report export` commands.

**Status: ✅ Fixed**
- `data/models.py` — `Conversation` gained `report_json: str` (full report serialized as JSON).
- `data/database.py` — `conversations.report_json TEXT DEFAULT ''` in the schema; `create_conversation()` persists it; `get_conversation()` / `list_conversations()` expose it. `init_db()` includes a lightweight migration (`ALTER TABLE ... ADD COLUMN report_json`) so pre-existing databases upgrade automatically.
- `ui/panels/chat.py` — after a swarm completes, the report JSON is written to the DB next to the conversation record.
- `cli/main.py` — implemented `swarmmind report show <id>` (rich rendering identical to the `ask` output) and `swarmmind report export <id> <path>` (Markdown file). Verified end-to-end against a seeded database: list / show / export / missing-id / corrupt-JSON (friendly error, exit 1) all behave correctly.
- `ui/panels/chat.py` — added `_load_past_conversations(st, state)` which loads `list_conversations(project_id)` from the DB on app start and on every project switch, and renders them in the History list (oldest first); clicking one restores it into the "last report" view. The once-per-project guard (`conversations_loaded_for`) lives in `st.session_state`, so it survives widget reruns. On project switch the history is reset to that project's conversations (never mixed across projects); in-session entries not yet persisted (no `_db_id`) are preserved. Studio's "Clear Conversation" still works — the flag stays set so cleared history isn't repopulated. Verified by scripted simulation: first load, rerun no-op, project switch/reset, unsaved-entry preservation, and clear semantics.

---

## 3. Multi-turn Conversation Context

**Problem:** Each query runs independently through the full pipeline (conductor → workers → synthesis). There is no context carried between turns, so follow-up questions like "expand on the second point" or "compare this with the previous finding" have no memory of prior exchanges.

**What exists:**
- `core/conductor.py` — `Conductor.decompose_query()` accepts `project_context` but not conversation history.
- `ui/panels/chat.py` — `conversation_history` in session state stores past queries and reports but is never fed back into the orchestrator.
- `core/orchestrator.py` — `run()` accepts `project_context` but not prior conversation context.

**Goal:** Pass previous conversation turns into the conductor and synthesis stages so follow-up questions can reference earlier answers, creating a coherent multi-turn research session.

**Status: ✅ Fixed**
- `core/orchestrator.py` — `run()` accepts `conversation_history: Optional[list[dict]]` and threads it into both `Conductor.decompose_query()` and `Synthesis.synthesize()`.
- `core/conductor.py` — `decompose_query()` accepts `conversation_history` and renders a "Previous Conversation" block (prior queries, report titles, executive summaries, key sections) into the user prompt.
- `core/synthesis.py` — `synthesize()` accepts `conversation_history` and includes a "Prior Conversation" block (prior queries, summaries, conclusions) so follow-ups reference earlier answers.
- `ui/panels/chat.py` — `worker_thread()` builds `conv_context` from `session_state.conversation_history` and passes it to `orchestrator.run(..., conversation_history=conv_context)`.
- History is fed from session state, which now includes past sessions' reports loaded from the DB (`_load_past_conversations`), so follow-ups can reference earlier findings even after a restart.
