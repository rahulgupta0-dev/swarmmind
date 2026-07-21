"""ChromaDB vector-store wrapper for SwarmMind."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

logger = logging.getLogger(__name__)


class ChromaStore:
    """Persistent vector store backed by ChromaDB.

    Each project gets its own named collection.  Chunks are stored with
    their text content and arbitrary metadata.
    """

    def __init__(self, persist_directory: str) -> None:
        self._client = chromadb.PersistentClient(
            path=persist_directory,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    @staticmethod
    def _collection_name(project_id: str) -> str:
        return f"project_{project_id}"

    def create_project_collection(self, project_id: str) -> None:
        """Create (or get) the collection for *project_id*."""
        name = self._collection_name(project_id)
        self._client.get_or_create_collection(name)
        logger.info("Collection '%s' ready", name)

    def delete_collection(self, project_id: str) -> None:
        """Delete the collection for *project_id*."""
        name = self._collection_name(project_id)
        try:
            self._client.delete_collection(name)
            logger.info("Collection '%s' deleted", name)
        except (ValueError, chromadb.errors.NotFoundError):
            logger.warning("Collection '%s' does not exist", name)
    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    def add_chunks(
        self,
        project_id: str,
        chunks: list[dict[str, Any]],
    ) -> int:
        """Add a batch of text chunks to the project's collection.

        Each *chunk* dict must contain:
            - ``text`` (str) — the chunk text.
            - ``metadata`` (dict) — arbitrary key-value pairs.

        Returns the number of chunks added.
        """
        name = self._collection_name(project_id)
        collection = self._client.get_or_create_collection(name)

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for chunk in chunks:
            ids.append(str(uuid.uuid4()))
            documents.append(chunk["text"])
            metadatas.append(chunk.get("metadata", {}))

        collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info("Added %d chunks to collection '%s'", len(chunks), name)
        return len(chunks)

    def search(
        self,
        project_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search the project collection for chunks relevant to *query*.

        Returns a list of dicts with keys ``text``, ``metadata``, ``distance``.
        """
        name = self._collection_name(project_id)
        collection = self._client.get_or_create_collection(name)

        results = collection.query(
            query_texts=[query],
            n_results=top_k,
        )

        output: list[dict[str, Any]] = []
        if not results["ids"] or not results["ids"][0]:
            return output

        for i in range(len(results["ids"][0])):
            output.append({
                "text": results["documents"][0][i] if results.get("documents") else "",
                "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                "distance": results["distances"][0][i] if results.get("distances") else 0.0,
            })

        return output
