"""SQLite database manager for SwarmMind."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, Optional

import aiosqlite

from swarmmind.data.models import Conversation, Note, Project, Source

logger = logging.getLogger(__name__)


class Database:
    """Async SQLite database manager.

    Usage::

        async with Database("swarmmind.db") as db:
            await db.init_db()
            project = await db.create_project(Project(name="Test"))
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the SQLite connection (create tables on first connect)."""
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA foreign_keys = ON")

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Context manager that yields the current connection."""
        if self._conn is None:
            await self.connect()
        assert self._conn is not None
        yield self._conn

    async def __aenter__(self) -> Database:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def init_db(self) -> None:
        """Create all tables if they don't exist."""
        async with self.connection() as conn:
            await conn.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    web_search_enabled INTEGER DEFAULT 1,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sources (
                    id            TEXT PRIMARY KEY,
                    project_id    TEXT NOT NULL,
                    source_type   TEXT NOT NULL,
                    source_uri    TEXT NOT NULL,
                    display_name  TEXT DEFAULT '',
                    status        TEXT DEFAULT 'pending',
                    char_count    INTEGER DEFAULT 0,
                    chunk_count   INTEGER DEFAULT 0,
                    error_message TEXT,
                    added_at      TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id              TEXT PRIMARY KEY,
                    project_id      TEXT NOT NULL,
                    query           TEXT NOT NULL,
                    web_search_used INTEGER DEFAULT 0,
                    worker_count    INTEGER DEFAULT 0,
                    report_json     TEXT DEFAULT '',
                    created_at      TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS notes (
                    id         TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title      TEXT DEFAULT '',
                    content    TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
            """)
            await conn.commit()

            # --- Lightweight migrations for pre-existing databases ---
            # (CREATE TABLE IF NOT EXISTS won't add columns to old tables)
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
            existing = {row[0] for row in await cursor.fetchall()}
            if "conversations" in existing:
                cursor = await conn.execute("PRAGMA table_info(conversations)")
                conv_cols = {row[1] for row in await cursor.fetchall()}
                if "report_json" not in conv_cols:
                    await conn.execute(
                        "ALTER TABLE conversations ADD COLUMN report_json TEXT DEFAULT ''")
                    await conn.commit()
                    logger.info("Migrated: added conversations.report_json")

            logger.info("Database initialised at %s", self._db_path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _new_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _ts(dt: Optional[datetime] = None) -> str:
        return (dt or datetime.utcnow()).isoformat()

    @staticmethod
    def _row_to_project(row: aiosqlite.Row) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            web_search_enabled=bool(row["web_search_enabled"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_source(row: aiosqlite.Row) -> Source:
        return Source(
            id=row["id"],
            project_id=row["project_id"],
            source_type=row["source_type"],
            source_uri=row["source_uri"],
            display_name=row["display_name"],
            status=row["status"],
            char_count=row["char_count"],
            chunk_count=row["chunk_count"],
            error_message=row["error_message"],
            added_at=datetime.fromisoformat(row["added_at"]),
        )

    @staticmethod
    def _row_to_conversation(row: aiosqlite.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            project_id=row["project_id"],
            query=row["query"],
            web_search_used=bool(row["web_search_used"]),
            worker_count=row["worker_count"],
            report_json=row["report_json"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _row_to_note(row: aiosqlite.Row) -> Note:
        return Note(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            content=row["content"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ------------------------------------------------------------------
    # CRUD — Projects
    # ------------------------------------------------------------------

    async def create_project(self, project: Project) -> Project:
        """Insert a new project, assigning an ID if missing."""
        if not project.id:
            project.id = self._new_id()
        now = self._ts()
        async with self.connection() as conn:
            await conn.execute(
                """INSERT INTO projects
                   (id, name, description, web_search_enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (project.id, project.name, project.description,
                 int(project.web_search_enabled), now, now),
            )
            await conn.commit()
        return project

    async def get_project(self, project_id: str) -> Optional[Project]:
        """Fetch a single project by ID."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,),
            )
            row = await cursor.fetchone()
        return self._row_to_project(row) if row else None

    async def list_projects(self) -> list[Project]:
        """Return all projects."""
        async with self.connection() as conn:
            cursor = await conn.execute("SELECT * FROM projects ORDER BY updated_at DESC")
            rows = await cursor.fetchall()
        return [self._row_to_project(r) for r in rows]

    async def update_project(self, project: Project) -> None:
        """Update an existing project."""
        now = self._ts()
        async with self.connection() as conn:
            await conn.execute(
                """UPDATE projects
                   SET name=?, description=?, web_search_enabled=?, updated_at=?
                   WHERE id=?""",
                (project.name, project.description,
                 int(project.web_search_enabled), now, project.id),
            )
            await conn.commit()

    async def delete_project(self, project_id: str) -> None:
        """Delete a project and its cascaded data."""
        async with self.connection() as conn:
            await conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            await conn.commit()

    # ------------------------------------------------------------------
    # CRUD — Sources
    # ------------------------------------------------------------------

    async def create_source(self, source: Source) -> Source:
        """Insert a new source."""
        if not source.id:
            source.id = self._new_id()
        if not source.added_at:
            source.added_at = datetime.utcnow()
        now = self._ts(source.added_at)
        async with self.connection() as conn:
            await conn.execute(
                """INSERT INTO sources
                   (id, project_id, source_type, source_uri, display_name,
                    status, char_count, chunk_count, error_message, added_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source.id, source.project_id, source.source_type,
                 source.source_uri, source.display_name, source.status,
                 source.char_count, source.chunk_count,
                 source.error_message, now),
            )
            await conn.commit()
        return source

    async def get_source(self, source_id: str) -> Optional[Source]:
        """Fetch a single source by ID."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM sources WHERE id = ?", (source_id,),
            )
            row = await cursor.fetchone()
        return self._row_to_source(row) if row else None

    async def list_sources(self, project_id: str) -> list[Source]:
        """List all sources for a project."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM sources WHERE project_id = ? ORDER BY added_at DESC",
                (project_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_source(r) for r in rows]

    async def update_source(self, source: Source) -> None:
        """Update an existing source."""
        async with self.connection() as conn:
            await conn.execute(
                """UPDATE sources
                   SET source_type=?, source_uri=?, display_name=?, status=?,
                       char_count=?, chunk_count=?, error_message=?
                   WHERE id=?""",
                (source.source_type, source.source_uri, source.display_name,
                 source.status, source.char_count, source.chunk_count,
                 source.error_message, source.id),
            )
            await conn.commit()

    async def update_source_status(
        self, source_id: str, status: str,
        char_count: int = 0, chunk_count: int = 0, error_message: Optional[str] = None,
    ) -> None:
        """Update only the ingestion-related fields of a source."""
        async with self.connection() as conn:
            await conn.execute(
                """UPDATE sources
                   SET status=?, char_count=?, chunk_count=?, error_message=?
                   WHERE id=?""",
                (status, char_count, chunk_count, error_message, source_id),
            )
            await conn.commit()

    async def delete_source(self, source_id: str) -> None:
        """Delete a source."""
        async with self.connection() as conn:
            await conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
            await conn.commit()

    # ------------------------------------------------------------------
    # CRUD — Conversations
    # ------------------------------------------------------------------

    async def create_conversation(self, conversation: Conversation) -> Conversation:
        """Insert a new conversation entry."""
        if not conversation.id:
            conversation.id = self._new_id()
        if not conversation.created_at:
            conversation.created_at = datetime.utcnow()
        now = self._ts(conversation.created_at)
        async with self.connection() as conn:
            await conn.execute(
                """INSERT INTO conversations
                   (id, project_id, query, web_search_used, worker_count, report_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (conversation.id, conversation.project_id, conversation.query,
                 int(conversation.web_search_used), conversation.worker_count,
                 conversation.report_json, now),
            )
            await conn.commit()
        return conversation

    async def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Fetch a single conversation by ID."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,),
            )
            row = await cursor.fetchone()
        return self._row_to_conversation(row) if row else None

    async def list_conversations(self, project_id: str) -> list[Conversation]:
        """List conversations for a project, newest first."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM conversations WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_conversation(r) for r in rows]

    async def delete_conversation(self, conversation_id: str) -> None:
        """Delete a conversation."""
        async with self.connection() as conn:
            await conn.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,),
            )
            await conn.commit()

    # ------------------------------------------------------------------
    # CRUD — Notes
    # ------------------------------------------------------------------

    async def create_note(self, note: Note) -> Note:
        """Insert a new note."""
        if not note.id:
            note.id = self._new_id()
        if not note.created_at:
            note.created_at = datetime.utcnow()
        if not note.updated_at:
            note.updated_at = note.created_at
        now = self._ts(note.created_at)
        now_upd = self._ts(note.updated_at)
        async with self.connection() as conn:
            await conn.execute(
                """INSERT INTO notes
                   (id, project_id, title, content, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (note.id, note.project_id, note.title, note.content, now, now_upd),
            )
            await conn.commit()
        return note

    async def list_notes(self, project_id: str) -> list[Note]:
        """List notes for a project, newest first."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM notes WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_note(r) for r in rows]

    async def update_note(self, note: Note) -> None:
        """Update an existing note."""
        now = self._ts()
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE notes SET title=?, content=?, updated_at=? WHERE id=?",
                (note.title, note.content, now, note.id),
            )
            await conn.commit()

    async def delete_note(self, note_id: str) -> None:
        """Delete a note."""
        async with self.connection() as conn:
            await conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            await conn.commit()
