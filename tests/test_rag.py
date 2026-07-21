"""Basic tests for RAG components."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from swarmmind.config import Config
from swarmmind.rag.chroma_store import ChromaStore


class TestChromaStore:
    """ChromaStore instantiation and basic operations."""

    @pytest.fixture
    def chroma_store(self) -> ChromaStore:
        """Create a temporary ChromaStore for testing."""
        tmp_dir = tempfile.mkdtemp()
        return ChromaStore(tmp_dir)

    def test_create_collection(self, chroma_store: ChromaStore) -> None:
        chroma_store.create_project_collection("test-project")
        # Should not raise

    def test_add_and_search(self, chroma_store: ChromaStore) -> None:
        project_id = "test-project"
        chroma_store.create_project_collection(project_id)

        chunks = [
            {"text": "RISC-V is an open ISA based on reduced instruction set computing.", "metadata": {"source": "doc1"}},
            {"text": "ARM SVE is a scalable vector extension for ARM architecture.", "metadata": {"source": "doc2"}},
            {"text": "AMD ROCm is a software stack for GPU computing.", "metadata": {"source": "doc3"}},
        ]

        count = chroma_store.add_chunks(project_id, chunks)
        assert count == 3

        results = chroma_store.search(project_id, "RISC-V architecture", top_k=2)
        assert len(results) <= 2
        for r in results:
            assert "text" in r
            assert "metadata" in r

    def test_delete_collection(self, chroma_store: ChromaStore) -> None:
        chroma_store.create_project_collection("to-delete")
        chroma_store.delete_collection("to-delete")
        # Should not raise on re-delete (silent)
        chroma_store.delete_collection("to-delete")
