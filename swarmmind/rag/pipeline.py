"""Document ingestion pipeline powered by MarkItDown."""

from __future__ import annotations

import io
import logging
import os
import re
import tempfile
from typing import Any, Optional

from markitdown import MarkItDown

from swarmmind.rag.chroma_store import ChromaStore

logger = logging.getLogger(__name__)

# Mapping of source types to markitdown file extensions / handlers
_SOURCE_TYPE_MAP: dict[str, str | None] = {
    "youtube": None,  # special handling (URL)
    "web": None,  # special handling (URL)
    "pdf": ".pdf",
    "docx": ".docx",
    "pptx": ".pptx",
    "xlsx": ".xlsx",
    "image": None,  # markitdown handles images natively
    "audio": None,  # markitdown audio support
    "epub": ".epub",
    "csv": ".csv",
    "json": ".json",
    "xml": ".xml",
    "text": ".txt",
    "zip": ".zip",
}


class Pipeline:
    """Orchestrates document conversion, chunking, and embedding storage.

    Args:
        config: Application config (used for RAG chunk settings).
        chroma_store: A :class:`ChromaStore` instance.
    """

    def __init__(self, config: Any, chroma_store: ChromaStore) -> None:
        self._config = config
        self._chroma_store = chroma_store
        self._markitdown = MarkItDown()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_source(
        self,
        source_type: str,
        source_input: str | bytes,
        source_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        """Ingest a single source through the pipeline.

        Args:
            source_type: One of the supported types (pdf, docx, web, …).
            source_input: File path (str), URL (str), or raw bytes.
            source_id: Unique identifier for the source (for metadata).
            project_id: The owning project.

        Returns:
            A dict with ``status``, ``chunk_count``, ``char_count`` and
            optionally ``error``.
        """
        try:
            text = self._convert(source_type, source_input)
        except Exception as exc:
            logger.exception("Conversion failed for source %s", source_id)
            return {
                "status": "error",
                "error": str(exc),
                "chunk_count": 0,
                "char_count": 0,
            }

        chunks = self._chunk_text(
            text,
            chunk_size=self._config.rag.chunk_size,
            chunk_overlap=self._config.rag.chunk_overlap,
        )

        # Build chunk dicts for ChromaDB
        chunk_dicts = []
        for idx, chunk_text in enumerate(chunks):
            chunk_dicts.append({
                "text": chunk_text,
                "metadata": {
                    "source_id": source_id,
                    "project_id": project_id,
                    "chunk_index": idx,
                    "source_type": source_type,
                },
            })

        self._chroma_store.add_chunks(project_id, chunk_dicts)

        logger.info(
            "Processed source %s: %d chars → %d chunks",
            source_id, len(text), len(chunks),
        )

        return {
            "status": "ready",
            "chunk_count": len(chunks),
            "char_count": len(text),
        }

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def _convert(self, source_type: str, source_input: str | bytes) -> str:
        """Convert *source_input* to plain text."""
        ext = _SOURCE_TYPE_MAP.get(source_type)

        # Web / YouTube URLs — pass directly as strings
        if source_type in ("web", "youtube"):
            assert isinstance(source_input, str), "URL source must be a string"
            result = self._markitdown.convert(source_input)
            return result.text_content

        # Raw bytes — write to a temporary file so markitdown can process it
        if isinstance(source_input, bytes):
            suffix = ext or ".bin"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(source_input)
                tmp_path = tmp.name
            try:
                result = self._markitdown.convert(tmp_path)
                return result.text_content
            finally:
                os.unlink(tmp_path)

        # File path (str) — convert directly
        assert isinstance(source_input, str), "Expected str path for non-URL types"
        if not os.path.isfile(source_input):
            raise FileNotFoundError(f"Source file not found: {source_input}")

        result = self._markitdown.convert(source_input)
        return result.text_content

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    @staticmethod
    def _chunk_text(
        text: str,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
    ) -> list[str]:
        """Split *text* into overlapping chunks.

        Prefers splitting by markdown headings (``##`` / ``###``) when
        possible; falls back to token (character) count.
        """
        # Try heading-based splitting first
        heading_pattern = re.compile(r"^#{2,4}\s.+", re.MULTILINE)
        splits = heading_pattern.split(text)
        splits = [s.strip() for s in splits if s.strip()]

        if len(splits) < 2:
            # No usable headings — character-based split
            splits = [text]

        chunks: list[str] = []
        for section in splits:
            if len(section) <= chunk_size:
                chunks.append(section)
            else:
                # Sub-chunk long sections
                start = 0
                while start < len(section):
                    end = min(start + chunk_size, len(section))
                    chunks.append(section[start:end])
                    start = end - chunk_overlap
                    if start >= len(section):
                        break

        return chunks
