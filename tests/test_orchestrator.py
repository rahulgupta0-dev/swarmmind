"""Basic tests for SwarmMind scaffolding."""

from __future__ import annotations

from pathlib import Path

import pytest

from swarmmind import __version__
from swarmmind.config import Config
from swarmmind.lemonade.client import LemonadeClient


class TestConfig:
    """Configuration loading and defaults."""

    def test_version(self) -> None:
        assert __version__ == "0.3.0"

    def test_default_config(self) -> None:
        """Config should load with sensible defaults (user config is isolated by conftest)."""
        config = Config()  # type: ignore[call-arg]
        assert config.lemonade.host == "localhost"
        assert config.lemonade.port == 13305
        assert config.rag.chunk_size == 512
        assert config.rag.chunk_overlap == 64
        assert config.rag.top_k == 5
        assert config.models.conductor == "Qwen3.6-35B-A3B-GGUF"
        assert config.models.worker == "Gemma-4-12B-it"
        assert config.models.embeddings == "nomic-embed-text-v1-GGUF"
        assert config.models.image == "Flux-2-Klein-4B"
        assert config.models.tts == "kokoro-v1"
        assert config.ui.theme == "light"
        assert config.ui.panel_layout == "balanced"

    def test_get_lemonade_base_url(self) -> None:
        config = Config()  # type: ignore[call-arg]
        assert config.get_lemonade_base_url() == "http://localhost:13305"

    def test_custom_lemonade_host(self) -> None:
        config = Config(lemonade={"host": "192.168.1.100", "port": 8080})  # type: ignore[call-arg]
        assert config.get_lemonade_base_url() == "http://192.168.1.100:8080"


class TestLemonadeClient:
    """LemonadeClient instantiation (no server needed)."""

    def test_instantiation(self) -> None:
        client = LemonadeClient("http://localhost:13305")
        assert client.base_url == "http://localhost:13305"
        assert client.api_key == "not-needed"

    def test_custom_url(self) -> None:
        client = LemonadeClient("http://192.168.1.50:13305", api_key="test-key")
        assert client.base_url == "http://192.168.1.50:13305"
        assert client.api_key == "test-key"
