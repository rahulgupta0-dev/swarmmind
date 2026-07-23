"""Configuration management for SwarmMind."""

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict, TomlConfigSettingsSource


class LemonadeConfig(BaseModel):
    """AMD Lemonade endpoint configuration."""

    host: str = "localhost"
    port: int = 13305


class RagConfig(BaseModel):
    """RAG pipeline configuration."""

    chunk_size: int = 512
    chunk_overlap: int = 64
    top_k: int = 5


class ModelsConfig(BaseModel):
    """Default model names, context windows, and per-role backend overrides.

    The ``backends`` map lets a user pin specific roles
    (``conductor``, ``worker``, ``embeddings``, ``image``, ``tts``) to a
    particular Lemonade backend (``rocm``, ``vulkan``, ``cpu``,
    ``ryzenai``, ``fastflowlm``, ``auto``).  When the backend is
    ``None`` (the default) the orchestrator auto-detects based on the
    hardware capabilities reported by ``/v1/system-info``.

    The ``context`` map sets the token context window per role. Default
    128K preserves Qwen3.6's thinking mode while saving VRAM versus the
    full 262K context.

    TOML shape::

        [models]
        conductor = "Qwen3.6-35B-A3B-GGUF"
        worker = "Gemma-4-12B-it"

        [models.context]
        conductor = 131072
        worker = 131072

        [models.backends]
        conductor = "rocm"
        embeddings = "ryzenai"

    A ``None``/missing ``backends`` value means "let hardware auto-detect choose".
    """

    conductor: str = "Qwen3.6-35B-A3B-GGUF"
    worker: str = "Gemma-4-12B-it"
    embeddings: str = "nomic-embed-text-v1-GGUF"
    image: str = "Flux-2-Klein-4B"
    tts: str = "kokoro-v1"
    router: str = "routing.router"

    # Per-role context window in tokens.  Defaults to 128K for LLM roles.
    # 128K preserves Qwen3.6's thinking capabilities (~262K native) while
    # saving significant VRAM compared to the full context.
    context: dict[str, int] = Field(
        default_factory=lambda: {
            "conductor": 131072,
            "worker": 131072,
            "embeddings": 8192,
            "router": 8192,
        },
    )

    # Free-form mapping: role -> backend hint. None means "auto".
    backends: dict[str, Optional[str]] = Field(
        default_factory=lambda: {
            "conductor": None,
            "worker": None,
            "embeddings": None,
            "image": None,
            "tts": None,
            "router": None,
        },
    )

    def is_router_model(self, model_name: str) -> bool:
        """Check if a model name refers to a Lemonade Router model (e.g. *.router)."""
        if not model_name:
            return False
        return model_name.endswith(".router") or "router" in model_name.lower()


class ExecutionConfig(BaseModel):
    """Worker execution mode configuration.

    Controls whether worker agents run in parallel (asyncio.gather) or
    sequentially. Parallel is faster on high-RAM systems (32 GB+) while
    sequential is safer on memory-constrained hardware (8-16 GB).

    The ``max_concurrent`` setting limits the number of workers that can
    run simultaneously even in ``parallel`` mode, useful for systems with
    limited VRAM or when running large models.
    """

    mode: Literal["parallel", "sequential"] = "parallel"
    max_concurrent: int = Field(default=4, ge=1, le=16)


class UiConfig(BaseModel):
    """UI configuration."""

    theme: Literal["light", "dark"] = "light"
    panel_layout: Literal["balanced", "wide", "compact"] = "balanced"


class Config(BaseSettings):
    """Top-level application configuration.

    Loaded from ~/.swarmmind/config.toml with sensible defaults.
    """

    lemonade: LemonadeConfig = LemonadeConfig()
    rag: RagConfig = RagConfig()
    models: ModelsConfig = ModelsConfig()
    execution: ExecutionConfig = ExecutionConfig()
    ui: UiConfig = UiConfig()

    model_config = SettingsConfigDict(
        env_prefix="SWARMMIND_",
        toml_file=str(Path.home() / ".swarmmind" / "config.toml"),
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """Inject TOML config source alongside env/init sources."""
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls),
        )

    def get_lemonade_base_url(self) -> str:
        """Return the full base URL for the Lemonade API endpoint."""
        return f"http://{self.lemonade.host}:{self.lemonade.port}"

    # ------------------------------------------------------------------
    # Backend helpers
    # ------------------------------------------------------------------

    def backend_for(self, role: str) -> Optional[str]:
        """Return the user-configured backend for *role*, or ``None``.

        ``role`` is one of ``"conductor"``, ``"worker"``,
        ``"embeddings"``, ``"image"``, ``"tts"``.  An unknown role
        returns ``None`` so callers fall back to AUTO.
        """
        return self.models.backends.get(role) or None
